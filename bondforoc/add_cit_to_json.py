"""
add_cit_to_json.py

Arricchisce un JSON di metadati bibliografici (formato WhoIsWho, dizionario
key -> paper) con identificativi e dati citazionali da OpenCitations.

Funzionamento:
  1. Carica da CSV la mappatura key -> {doi, omid} (colonne: key, doi, omid).
  2. Per ogni paper del JSON presente nella mappatura aggiunge i campi 'doi' e 'omid'.
  3. Se ONLY_ADD_IDS = False, interroga l'OpenCitations Index API v2 e aggiunge:
       - outgoing_citations / outgoing_citations_count  (endpoint /references)
       - incoming_citations / incoming_citations_count  (endpoint /citations)
  4. Salva nel JSON di output solo i paper presenti nella mappatura.

Input:   INPUT_JSON (metadati), CSV_MAPPING (key, doi, omid)
Output:  OUTPUT_JSON (metadati arricchiti)
File ausiliari: citations_cache.json (cache delle risposte API),
                process_metadata.log (log di esecuzione)

Note: DOI normalizzati (minuscolo, senza prefissi); gestione rate limit con
ritardo fisso, retry ed exponential backoff sugli errori 429; cache salvata
ogni CACHE_SAVE_INTERVAL paper. Richiede conferma interattiva prima dell'avvio.

"""

import requests
import json
import logging
import os
import time
import csv
from typing import Dict, List, Optional, Tuple

# ===============================
# CONFIGURAZIONE MODALITÀ
# ===============================
# TRUE: Aggiunge solo DOI e OMID dal CSV (modalità silenziosa).
# FALSE: Aggiunge gli ID e scarica anche le citazioni dalle API.
ONLY_ADD_IDS = False

# ===============================
# CONFIGURAZIONE PERCORSI
# ===============================
#INPUT_JSON = r"C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BondforOC\data\Bondvalidation.json"
INPUT_JSON = r"C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BOND-OC\WhoIsWho\bond\dataset\data\src\sna-test\sna_test_pub.json"
CSV_MAPPING = r"C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BondforOC\results\Bond_test_crossref_validated\validated_keys_dois.csv"
OUTPUT_JSON = r"C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BondforOC\results\test_metadata_with_ids_and_citations.json"

CACHE_FILENAME = "citations_cache.json"
LOG_FILENAME = "process_metadata.log"

# API OpenCitations
OPENCITATION_CITATIONS_URL = "https://api.opencitations.net/index/v2/citations/"
OPENCITATION_REFERENCES_URL = "https://api.opencitations.net/index/v2/references/"
OPENCITATIONS_ACCESS_TOKEN = "234538a5-b679-4f83-846a-c3e7ebaedec0"

# Parametri rate limiting
RATE_LIMIT_DELAY = 0.3
MAX_RETRIES = 5
BACKOFF_FACTOR = 2
CACHE_SAVE_INTERVAL = 50

# ===============================
# SETUP LOGGING
# ===============================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILENAME, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# ===============================
# CACHE MANAGEMENT
# ===============================
cache = {}
cache_modified = False

def load_cache():
    global cache
    if os.path.exists(CACHE_FILENAME):
        try:
            with open(CACHE_FILENAME, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            logging.info(f"Cache caricata con {len(cache)} elementi")
        except Exception as e:
            logging.error(f"Errore nel caricamento della cache: {e}")
            cache = {}

def save_cache():
    global cache_modified
    if not cache_modified or ONLY_ADD_IDS: return
    try:
        with open(CACHE_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=4)
        logging.info(f"Cache salvata con {len(cache)} elementi")
        cache_modified = False
    except Exception as e:
        logging.error(f"Errore nel salvataggio della cache: {e}")

# ===============================
# UTILITY FUNCTIONS
# ===============================
def normalize_doi(doi: Optional[str]) -> Optional[str]:
    if not doi or doi == "None": return None
    if isinstance(doi, str):
        doi = doi.lower().strip()
        for prefix in ["doi:", "https://doi.org/", "http://doi.org/", "doi.org/"]:
            if doi.startswith(prefix):
                doi = doi[len(prefix):]
        return doi
    return doi

# ===============================
# API OPENCITATIONS
# ===============================
def get_citations(doi: str) -> Tuple[bool, List[str], Optional[str]]:
    normalized_doi = normalize_doi(doi)
    if not normalized_doi: return False, [], None
    cache_key = f"cit_{normalized_doi}"
    if cache_key in cache:
        return cache[cache_key]['success'], cache[cache_key]['citations'], cache[cache_key].get('error')
    
    headers = {"authorization": OPENCITATIONS_ACCESS_TOKEN.strip()}
    for attempt in range(MAX_RETRIES):
        try:
            url = f"{OPENCITATION_CITATIONS_URL}doi:{normalized_doi}"
            time.sleep(RATE_LIMIT_DELAY)
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 429:
                time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
                continue
            if response.status_code == 404:
                return True, [], None
            response.raise_for_status()
            data = response.json()
            citing_dois = [normalize_doi(item.get('citing')) for item in data if item.get('citing')]
            citing_dois = [d for d in citing_dois if d]
            cache[cache_key] = {'success': True, 'citations': citing_dois, 'error': None}
            global cache_modified; cache_modified = True
            return True, citing_dois, None
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                return False, [], str(type(e).__name__)
            time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
    return False, [], "MaxRetriesExceeded"

def get_references(doi: str) -> Tuple[bool, List[str], Optional[str]]:
    normalized_doi = normalize_doi(doi)
    if not normalized_doi: return False, [], None
    cache_key = f"ref_{normalized_doi}"
    if cache_key in cache:
        return cache[cache_key]['success'], cache[cache_key]['references'], cache[cache_key].get('error')
    
    headers = {"authorization": OPENCITATIONS_ACCESS_TOKEN.strip()}
    for attempt in range(MAX_RETRIES):
        try:
            url = f"{OPENCITATION_REFERENCES_URL}doi:{normalized_doi}"
            time.sleep(RATE_LIMIT_DELAY)
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 429:
                time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
                continue
            if response.status_code == 404:
                return True, [], None
            response.raise_for_status()
            data = response.json()
            cited_dois = [normalize_doi(item.get('cited')) for item in data if item.get('cited')]
            cited_dois = [d for d in cited_dois if d]
            cache[cache_key] = {'success': True, 'references': cited_dois, 'error': None}
            global cache_modified; cache_modified = True
            return True, cited_dois, None
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                return False, [], str(type(e).__name__)
            time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
    return False, [], "MaxRetriesExceeded"

# ===============================
# MAIN PROCESSING
# ===============================

def load_key_id_mapping(csv_path: str) -> Dict[str, Dict[str, str]]:
    """Carica mappatura key -> {doi, omid}."""
    mapping = {}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get('key', '').strip()
                doi = row.get('doi', '').strip()
                omid = row.get('omid', '').strip()
                if key:
                    mapping[key] = {
                        'doi': normalize_doi(doi),
                        'omid': omid if omid else None
                    }
        logging.info(f"Caricata mappatura per {len(mapping)} chiavi")
        return mapping
    except Exception as e:
        logging.error(f"Errore nel caricamento del CSV: {e}"); raise

def add_data_to_papers(papers_data: Dict, key_id_mapping: Dict[str, Dict[str, str]]) -> Dict:
    papers_to_process = {k: v for k, v in papers_data.items() if k in key_id_mapping}
    processed = 0
    updated_papers = {}

    for key, paper in papers_to_process.items():
        processed += 1
        ids = key_id_mapping[key]
        doi, omid = ids['doi'], ids['omid']
        
        # --- AGGIUNTA ID AI METADATI ---
        paper['doi'] = doi
        paper['omid'] = omid
        logging.info(f"[{processed}/{len(papers_to_process)}] Key={key} | DOI={doi}")

        # --- RECUPERO API (Opzionale) ---
        if not ONLY_ADD_IDS and doi:
            # References
            ok_ref, refs, err_ref = get_references(doi)
            paper['outgoing_citations'] = refs if ok_ref else []
            paper['outgoing_citations_count'] = len(refs) if ok_ref else 0
            
            # Citations
            ok_cit, cites, err_cit = get_citations(doi)
            paper['incoming_citations'] = cites if ok_cit else []
            paper['incoming_citations_count'] = len(cites) if ok_cit else 0
        
        updated_papers[key] = paper
        if not ONLY_ADD_IDS and processed % CACHE_SAVE_INTERVAL == 0: save_cache()

    return updated_papers

def main():
    try:
        load_cache()
        mapping = load_key_id_mapping(CSV_MAPPING)
        with open(INPUT_JSON, 'r', encoding='utf-8') as f:
            papers_data = json.load(f)

        print(f"\nModalità: {'ID-ONLY (Silenziosa)' if ONLY_ADD_IDS else 'FULL (API + IDs)'}")
        print(f"Papers da processare: {len([k for k in papers_data if k in mapping])}")
        
        if input("\nProcedere? (s/n): ").lower() not in ['s', 'si', 'y']: return

        updated_data = add_data_to_papers(papers_data, mapping)
        
        with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(updated_data, f, indent=4, ensure_ascii=False)
        
        save_cache()
        print(f"\nCompletato! File salvato in: {OUTPUT_JSON}")
    except Exception as e:
        logging.exception("Errore critico"); save_cache()

if __name__ == '__main__': main()