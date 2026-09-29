"""
step3_validate_crossref_dois.py — Stage 3: validazione Crossref su larga scala

Applica il cutoff calibrato nello Stage 2 all'intero benchmark per assegnare un
DOI a ogni pubblicazione.

Funzionamento:
  1. Per ogni pubblicazione del JSON di input interroga Crossref per titolo e anno.
  2. Accetta il primo risultato solo se score >= cutoff e se il match supera
     la validazione sui metadati (titolo + anno).
  3. Classifica ogni record come validated, rejected o error.
  Le query sono eseguite in parallelo (multiprocessing) con una cache condivisa
  tra i processi.

Funzioni principali:
  process_item()                          query e validazione di un singolo record
  crossref_with_metavalidation_pipeline() orchestrazione: pool di processi,
                                          gestione della cache, salvataggio output

Input:   JSON delle pubblicazioni (formato WhoIsWho/BOND), cutoff (default 35.0)
Output:  validated_keys_dois.csv (key, doi), rejected_items.csv,
         error_items.csv, crossref_cache.json

Dipendenze: tqdm, step2_calibrate_crossref_cutoff.py (funzioni di query e validazione)
"""


import json
import os
import time
import csv
import multiprocessing as mp
from functools import partial
from tqdm import tqdm
from typing import Dict, List, Optional, Tuple, Union, Any

from step2_calibrate_crossref_cutoff import (
    load_crossref_cache,
    save_crossref_cache,
    query_with_retry,
    query_crossref,
    extract_crossref_score,
    extract_crossref_metadata,
    validate_crossref_match,
    normalize_doi
)

def process_item(item_data, cutoff, crossref_cache, cache_lock, cache_file=None, use_cache=True):
    """
    Process a single item for Crossref validation.
    
    Args:
        item_data: Tuple of (key, item_dict)
        cutoff: Score cutoff for validation
        crossref_cache: Shared dictionary for caching
        cache_lock: Lock for thread-safe cache operations
        cache_file: File to save cache updates
        use_cache: Whether to use cache
        
    Returns:
        Dictionary with processing results and cache update info
    """
    key, item = item_data
    
    result = {
        "key": key,
        "status": None,  # 'validated', 'rejected', or 'error'
        "data": {},
        "cache_updated": False
    }
    
    title = item.get("title", "")
    year = item.get("year", 2020)
    
    if not title:
        result["status"] = "rejected"
        result["data"] = {
            "title": title,
            "reason": "No title found"
        }
        return result
    
    try:
        # Genera una chiave cache basata su titolo e anno
        cache_key = f"{title}_{year}"
        cr_doi = None
        cr_score = 0
        cr_metadata = {}
        cache_hit = False
        
        # Controlla se abbiamo questo risultato nella cache
        with cache_lock:
            if cache_key in crossref_cache and use_cache:
                print(f"Usando risultato dalla cache per '{title}'")
                cr_result = crossref_cache[cache_key]
                cr_doi = cr_result.get("doi")
                cr_score = cr_result.get("score", 0)
                cr_metadata = cr_result.get("metadata", {})
                cache_hit = True
        
        # Se non c'è nella cache, esegui la query
        if not cache_hit:
            print(f"  Interrogazione di CrossRef per '{title}'...")
            crossref_results = query_with_retry(query_crossref, title=title, year=year)
            
            cr_items = crossref_results.get("message", {}).get("items", [])
            if cr_items:
                cr_item = cr_items[0]  
                cr_doi = cr_item.get("DOI")
                cr_score = extract_crossref_score(cr_item)
                cr_metadata = extract_crossref_metadata(cr_item)
            else:
                print(f"Nessun risultato trovato su CrossRef per {key}")
                cr_doi = None
                cr_score = 0
                cr_metadata = {}
            
            # Aggiorna la cache
            with cache_lock:
                crossref_cache[cache_key] = {
                    "doi": cr_doi,
                    "score": cr_score,
                    "metadata": cr_metadata,
                    "key": key  
                }
                result["cache_updated"] = True
        
        normalized_doi = normalize_doi(cr_doi)
        
        if cr_score < cutoff:
            result["status"] = "rejected"
            result["data"] = {
                "title": title,
                "doi": normalized_doi or cr_doi,
                "score": cr_score,
                "reason": f"Score {cr_score} below cutoff {cutoff}"
            }
            return result
        
        is_valid_match, validation_details = validate_crossref_match(
            item, cr_metadata, cr_metadata.get("year")
        )
        
        if not is_valid_match:
            result["status"] = "rejected"
            result["data"] = {
                "title": title,
                "doi": normalized_doi or cr_doi,
                "score": cr_score,
                "reason": f"Metadata validation failed: {validation_details}"
            }
            return result
        
        result["status"] = "validated"
        result["data"] = {
            "doi": normalized_doi or cr_doi
        }
        
        # Add a small delay to avoid overwhelming the Crossref API
        # Solo se abbiamo effettivamente fatto una chiamata API
        if not cache_hit:
            time.sleep(0.5)
        
    except Exception as e:
        result["status"] = "error"
        result["data"] = {
            "title": title,
            "error": str(e)
        }
    
    return result


def crossref_with_metavalidation_pipeline(
    input_json_path: str,
    cutoff: float,
    output_dir: str = "results",
    output_file: str = "validated_keys_dois.csv",
    num_processes: int = None,
    use_cache: bool = True,
    cache_file: str = None
) -> None:
    """
    Pipeline for validating resources with Crossref and extracting their DOIs.
    Uses multiprocessing to speed up the process and includes a progress bar.
    
    Args:
        input_json_path: Path to the Bondvalidation.json file
        cutoff: Manual cutoff for Crossref score
        output_dir: Directory to save results
        output_file: Filename for the output CSV
        num_processes: Number of processes to use (defaults to cpu_count-1)
        use_cache: Whether to use cache for CrossRef queries
        cache_file: File to store CrossRef cache
    """
    os.makedirs(output_dir, exist_ok=True)
    
    output_path = os.path.join(output_dir, output_file)
    rejected_path = os.path.join(output_dir, "rejected_items.csv")
    error_path = os.path.join(output_dir, "error_items.csv")
    
    if cache_file is None and use_cache:
        cache_file = os.path.join(output_dir, "crossref_cache.json")
    
    # Carica la cache esistente se disponibile
    crossref_cache = {}
    if use_cache and cache_file:
        crossref_cache = load_crossref_cache(cache_file)
        print(f"Cache Crossref caricata con {len(crossref_cache)} elementi")
    
    with open(input_json_path, "r", encoding="utf-8") as f:
        input_json = json.load(f)
    
    # Determine the number of processes to use
    if num_processes is None:
        num_processes = max(1, mp.cpu_count() - 1)  # Use all cores minus one by default
    
    print(f"Processing {len(input_json)} items with cutoff {cutoff} using {num_processes} processes...")
    
    items_to_process = list(input_json.items())
    
    # Set up multiprocessing with a shared cache
    manager = mp.Manager()
    shared_cache = manager.dict(crossref_cache)
    cache_lock = manager.Lock()
    
    pool = mp.Pool(processes=num_processes)
    process_func = partial(
        process_item, 
        cutoff=cutoff,
        crossref_cache=shared_cache,
        cache_lock=cache_lock,
        cache_file=cache_file,
        use_cache=use_cache
    )
    
    results = []
    cache_updates = False
    
    for result in tqdm(
        pool.imap_unordered(process_func, items_to_process),
        total=len(items_to_process),
        desc="Validating items"
    ):
        results.append(result)
        if result.get("cache_updated", False):
            cache_updates = True
    
    pool.close()
    pool.join()
    
    # Aggiorna la cache principale con i risultati dal manager
    if use_cache and cache_updates:
        crossref_cache.update(dict(shared_cache))
        if cache_file:
            save_crossref_cache(crossref_cache, cache_file)
            print(f"Cache Crossref aggiornata e salvata con {len(crossref_cache)} elementi")
    
    validated_items = []
    rejected_items = []
    error_items = []
    
    for result in results:
        if result["status"] == "validated":
            validated_item = {"key": result["key"]}
            validated_item.update(result["data"])
            validated_items.append(validated_item)
        elif result["status"] == "rejected":
            rejected_item = {"key": result["key"]}
            rejected_item.update(result["data"])
            rejected_items.append(rejected_item)
        elif result["status"] == "error":
            error_item = {"key": result["key"]}
            error_item.update(result["data"])
            error_items.append(error_item)
    
    if validated_items:
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["key", "doi"])
            writer.writeheader()
            writer.writerows(validated_items)
    
    if rejected_items:
        with open(rejected_path, "w", encoding="utf-8", newline="") as f:
            fieldnames = list(set().union(*(item.keys() for item in rejected_items)))
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rejected_items)
    
    if error_items:
        with open(error_path, "w", encoding="utf-8", newline="") as f:
            fieldnames = list(set().union(*(item.keys() for item in error_items)))
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(error_items)
    
    stats = {
        "total": len(input_json),
        "validated": len(validated_items),
        "rejected": len(rejected_items),
        "errors": len(error_items)
    }
    
    print(f"\nProcessing complete.")
    print(f"Validated items saved to: {output_path}")
    print(f"Rejected items saved to: {rejected_path}")
    print(f"Error items saved to: {error_path}")
    print(f"Total items: {stats['total']}")
    print(f"Validated: {stats['validated']}")
    print(f"Rejected: {stats['rejected']}")
    print(f"Errors: {stats['errors']}")

if __name__ == "__main__":
    input_file = r"C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BOND-OC\WhoIsWho\bond\dataset\data\src\sna-test\sna_test_pub.json"
    manual_cutoff = 35.0  
    output_dir = "results/Bond_test_crossref_validated"
    cache_file = "results/crossref_cache.json"  # Specifica il file di cache
    
    crossref_with_metavalidation_pipeline(
        input_json_path=input_file,
        cutoff=manual_cutoff,
        output_dir=output_dir,
        num_processes=4,  # Puoi cambiare questo valore in base alle tue esigenze
        use_cache=True,    # Attiva l'uso della cache
        cache_file=cache_file)
    