import requests
import json
import logging
import os
import time
import signal
import sys
import re
from typing import Dict, List, Optional, Tuple, Any

# Configurazione - API V2 AGGIORNATA
OPENCITATION_META_URL = "https://api.opencitations.net/meta/v1/metadata/"
OPENCITATION_CITATIONS_URL = "https://api.opencitations.net/index/v2/citations/"
OPENCITATION_REFERENCES_URL = "https://api.opencitations.net/index/v2/references/"
INPUT_FILENAME = r"C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BondforOC\results\Bond_test_crossref_validated\validated_keys_dois.csv"

# Cartelle di output
BASE_OUTPUT_DIR = r"C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BondforOC\results"
OUTPUT_DIR_STANDARD = os.path.join(BASE_OUTPUT_DIR, "OC_results")
OUTPUT_DIR_WITH_CITATIONS = os.path.join(BASE_OUTPUT_DIR, "OC_test_results_with_citations")

# Variabile globale per la modalità corrente
INCLUDE_CITATIONS = False
OUTPUT_DIR = OUTPUT_DIR_STANDARD

OPENCITATIONS_ACCESS_TOKEN = "234538a5-b679-4f83-846a-c3e7ebaedec0"
CACHE_SAVE_INTERVAL = 100
RATE_LIMIT_DELAY = 0.3
MAX_RETRIES = 5
BACKOFF_FACTOR = 2
TEST_BATCH_SIZE = 100

RETRY_ERROR_CODES = [429, 500, 502, 503, 504]
RETRY_EXCEPTIONS = ['Timeout', 'ConnectionError', 'ReadTimeout', 'ConnectTimeout', 'EmptyResponse']

# File di output
CACHE_FILENAME = ""
NOT_FOUND_FILENAME = ""
METADATA_FILENAME = ""
CONVERTED_FILENAME = ""
LOG_FILENAME = ""
SUMMARY_FILENAME = ""
RETRY_FILENAME = ""

cache = {}
processed_count = 0
cache_modified = False

def setup_output_paths():
    """Configura i percorsi dei file in base alla modalità selezionata."""
    global CACHE_FILENAME, NOT_FOUND_FILENAME, METADATA_FILENAME, CONVERTED_FILENAME
    global LOG_FILENAME, SUMMARY_FILENAME, RETRY_FILENAME, OUTPUT_DIR
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    CACHE_FILENAME = os.path.join(OUTPUT_DIR, "opencitations_cache.json")
    NOT_FOUND_FILENAME = os.path.join(OUTPUT_DIR, "final_batch_notfound.json")
    METADATA_FILENAME = os.path.join(OUTPUT_DIR, "opencitations_metadata.json")
    CONVERTED_FILENAME = os.path.join(OUTPUT_DIR, "converted_metadata.json")
    LOG_FILENAME = os.path.join(OUTPUT_DIR, "opencitations_app.log")
    SUMMARY_FILENAME = os.path.join(OUTPUT_DIR, "processing_summary.json")
    RETRY_FILENAME = os.path.join(OUTPUT_DIR, "retry_candidates.json")

def setup_logging():
    """Configura il logging dopo aver impostato i percorsi."""
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILENAME, encoding='utf-8'),
            logging.StreamHandler()
        ],
        force=True
    )

def select_mode():
    """Chiede all'utente quale modalità utilizzare."""
    global INCLUDE_CITATIONS, OUTPUT_DIR
    
    print("\n" + "="*60)
    print("SELEZIONA MODALITÀ DI ESECUZIONE")
    print("="*60)
    print("1. Modalità Standard (solo metadati)")
    print("2. Modalità con Citazioni (metadati + citazioni in entrata/uscita)")
    print("="*60)
    
    while True:
        choice = input("Seleziona modalità (1 o 2): ").strip()
        
        if choice == '1':
            INCLUDE_CITATIONS = False
            OUTPUT_DIR = OUTPUT_DIR_STANDARD
            print("\n✓ Modalità Standard selezionata")
            print(f"  Output in: {OUTPUT_DIR}")
            break
        elif choice == '2':
            INCLUDE_CITATIONS = True
            OUTPUT_DIR = OUTPUT_DIR_WITH_CITATIONS
            print("\n✓ Modalità con Citazioni selezionata")
            print(f"  Output in: {OUTPUT_DIR}")
            print("  ATTENZIONE: Questa modalità richiede circa 3x più tempo!")
            confirm = input("  Vuoi continuare? (s/n): ").strip().lower()
            if confirm in ['s', 'si', 'y', 'yes']:
                break
            else:
                continue
        else:
            print("Scelta non valida. Inserisci 1 o 2.")
    
    print("="*60 + "\n")

# ===============================
# FUNZIONI PER RECUPERO CITAZIONI
# ===============================

def get_citations(doi: str) -> Tuple[bool, List[str], Optional[str]]:
    """
    Recupera le citazioni in entrata (chi cita questo DOI).
    Restituisce: (successo, lista_doi_citanti, tipo_errore)
    """
    normalized_doi = normalize_doi(doi)
    if not normalized_doi:
        return False, [], None
    
    headers = {"authorization": OPENCITATIONS_ACCESS_TOKEN.strip()}
    
    for attempt in range(MAX_RETRIES):
        try:
            url = f"{OPENCITATION_CITATIONS_URL}doi:{normalized_doi}"
            time.sleep(RATE_LIMIT_DELAY)
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 429:
                wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                logging.warning(f"Rate limit per citations. Attesa {wait_time}s")
                time.sleep(wait_time)
                continue
            
            if response.status_code in [500, 502, 503, 504]:
                wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                time.sleep(wait_time)
                continue
            
            if response.status_code == 404:
                return True, [], None
            
            if not response.text or response.text.strip() == "":
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
                    continue
                else:
                    return True, [], None
            
            response.raise_for_status()
            
            try:
                data = response.json()
            except json.JSONDecodeError:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
                    continue
                else:
                    return True, [], None
            
            citing_dois = []
            if isinstance(data, list):
                for item in data:
                    citing_doi = item.get('citing')
                    if citing_doi:
                        citing_doi = citing_doi.replace('doi:', '')
                        citing_dois.append(normalize_doi(citing_doi))
            
            return True, citing_dois, None
            
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                logging.warning(f"Errore nel recupero citations: {e}")
                return False, [], str(type(e).__name__)
            time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
    
    return False, [], "MaxRetriesExceeded"

def get_references(doi: str) -> Tuple[bool, List[str], Optional[str]]:
    """
    Recupera le citazioni in uscita (references).
    Restituisce: (successo, lista_doi_citati, tipo_errore)
    """
    normalized_doi = normalize_doi(doi)
    if not normalized_doi:
        return False, [], None
    
    headers = {"authorization": OPENCITATIONS_ACCESS_TOKEN.strip()}
    
    for attempt in range(MAX_RETRIES):
        try:
            url = f"{OPENCITATION_REFERENCES_URL}doi:{normalized_doi}"
            time.sleep(RATE_LIMIT_DELAY)
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 429:
                wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                logging.warning(f"Rate limit per references. Attesa {wait_time}s")
                time.sleep(wait_time)
                continue
            
            if response.status_code in [500, 502, 503, 504]:
                wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                time.sleep(wait_time)
                continue
            
            if response.status_code == 404:
                return True, [], None
            
            if not response.text or response.text.strip() == "":
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
                    continue
                else:
                    return True, [], None
            
            response.raise_for_status()
            
            try:
                data = response.json()
            except json.JSONDecodeError:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
                    continue
                else:
                    return True, [], None
            
            cited_dois = []
            if isinstance(data, list):
                for item in data:
                    cited_doi = item.get('cited')
                    if cited_doi:
                        cited_doi = cited_doi.replace('doi:', '')
                        cited_dois.append(normalize_doi(cited_doi))
            
            return True, cited_dois, None
            
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                logging.warning(f"Errore nel recupero references: {e}")
                return False, [], str(type(e).__name__)
            time.sleep(RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt))
    
    return False, [], "MaxRetriesExceeded"

# ===============================
# FUNZIONI DI CONVERSIONE FORMATO
# ===============================

def parse_authors(author_string: str) -> List[Dict[str, str]]:
    """
    Converte la stringa degli autori nel formato desiderato.
    La META API fornisce autori nel formato: "Cognome, Nome [orcid:... omid:...]; Cognome2, Nome2 [...]"
    """
    if not author_string:
        return []
    
    authors = []
    # Dividi per punto e virgola per separare gli autori
    author_parts = author_string.split(';')
    
    # Parole che indicano inizio di affiliazioni (la META API non dovrebbe averle, ma per sicurezza)
    affiliation_indicators = {
        'department', 'university', 'college', 'institute', 'laboratory', 
        'school', 'center', 'centre', 'hospital', 'faculty', 'division'
    }
    
    for author_part in author_parts:
        author_part = author_part.strip()
        if not author_part:
            continue
        
        # Rimuovi ORCID e OMID PRIMA di qualsiasi altra elaborazione
        # Formato: [orcid:0000-0002-2030-3813 omid:ra/0615011235018]
        author_part = re.sub(r'\[orcid:[^\]]+\]', '', author_part)
        author_part = re.sub(r'\[omid:[^\]]+\]', '', author_part)
        author_part = re.sub(r'\[[^\]]*orcid[^\]]*\]', '', author_part)
        author_part = re.sub(r'\[[^\]]*omid[^\]]*\]', '', author_part)
        
        # Pulisci spazi multipli
        author_part = re.sub(r'\s+', ' ', author_part).strip()
        
        # Rimuovi virgole finali
        author_part = author_part.rstrip(',').strip()
        
        if not author_part:
            continue
        
        # SKIP: Se questa parte contiene indicatori di affiliazione
        author_lower = author_part.lower()
        if any(indicator in author_lower for indicator in affiliation_indicators):
            continue
        
        # SKIP: Se contiene numeri lunghi (dopo aver rimosso ORCID/OMID)
        if re.search(r'\d{4,}', author_part):
            continue
            
        # SKIP: Se è troppo lungo per essere un nome
        if len(author_part) > 50:
            continue
        
        # Parsing del nome
        # La META API usa il formato "Cognome, Nome"
        if ',' in author_part:
            parts = author_part.split(',', 1)
            if len(parts) == 2:
                surname = parts[0].strip()
                name = parts[1].strip()
                if name and surname:
                    full_name = f"{name} {surname}"
                else:
                    full_name = (name or surname).strip()
            else:
                full_name = author_part.strip()
        else:
            full_name = author_part.strip()
        
        if full_name and len(full_name) > 2:
            authors.append({
                "name": full_name,
                "org": ""
            })
    
    return authors

def extract_keywords_from_title(title: str) -> List[str]:
    """Estrae parole chiave dal titolo."""
    if not title:
        return []
    
    stop_words = {
        'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 
        'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'that', 'the', 
        'to', 'was', 'were', 'will', 'with', 'between', 'using', 'through',
        'this', 'these', 'those', 'their', 'them', 'than', 'when', 'where',
        'which', 'while', 'who', 'how', 'what', 'can', 'could', 'should',
        'would', 'may', 'might', 'must', 'shall', 'study', 'analysis'
    }
    
    words = re.findall(r'\b[a-zA-Z]+\b', title.lower())
    keywords = [word for word in words if len(word) > 3 and word not in stop_words]
    
    seen = set()
    unique_keywords = []
    for keyword in keywords:
        if keyword not in seen:
            seen.add(keyword)
            unique_keywords.append(keyword)
    
    return unique_keywords[:15]

def parse_year(year_string: str) -> Optional[int]:
    """Estrae l'anno dalla stringa anno."""
    if not year_string:
        return None
    
    year_match = re.search(r'\b(19|20)\d{2}\b', str(year_string))
    if year_match:
        return int(year_match.group())
    
    return None

def convert_metadata_format(opencitations_data: List[Dict]) -> Dict[str, Dict]:
    """
    Converte i dati dal formato OpenCitations META al formato target.
    """
    converted_data = {}
    
    for paper in opencitations_data:
        key = paper.get("key")
        doi = paper.get("doi")
        if not key:
            continue
            
        metadata_list = paper.get("metadata", [])
        if not metadata_list:
            continue
            
        metadata = metadata_list[0] if isinstance(metadata_list, list) else metadata_list
        
        title = metadata.get("title", "")
        authors_string = metadata.get("author", "")
        year_string = metadata.get("pub_date", "")
        venue = metadata.get("venue", "")
        
        converted_paper = {
            "id": key,
            "doi": doi,
            "title": title,
            "abstract": "",
            "keywords": extract_keywords_from_title(title),
            "authors": parse_authors(authors_string),
            "venue": venue,
            "year": parse_year(year_string)
        }
        
        if INCLUDE_CITATIONS:
            converted_paper["outgoing_citations"] = paper.get("outgoing_citations", [])
            converted_paper["incoming_citations"] = paper.get("incoming_citations", [])
            converted_paper["outgoing_citations_count"] = len(paper.get("outgoing_citations", []))
            converted_paper["incoming_citations_count"] = len(paper.get("incoming_citations", []))
        
        converted_data[key] = converted_paper
    
    return converted_data

# ===============================
# FUNZIONI ORIGINALI
# ===============================

def signal_handler(sig, frame):
    logging.info("Interruzione rilevata, salvataggio della cache...")
    save_cache()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def is_retryable_error(error_code=None, exception_type=None):
    if error_code and error_code in RETRY_ERROR_CODES:
        return True
    if exception_type and any(retry_type in str(exception_type) for retry_type in RETRY_EXCEPTIONS):
        return True
    return False

def normalize_doi(doi: Optional[str]) -> Optional[str]:
    if not doi or doi == "None":
        return None
    
    if isinstance(doi, str):
        if doi.startswith("doi:"):
            doi = doi[4:]
        if doi.startswith("https://doi.org/"):
            doi = doi[16:]
        elif doi.startswith("http://doi.org/"):
            doi = doi[15:]
        elif doi.startswith("doi.org/"):
            doi = doi[8:]
        elif doi.startswith("DOI:"):
            doi = doi[4:].strip()
    
        return doi.lower().strip()

    return doi

def load_cache():
    global cache
    if os.path.exists(CACHE_FILENAME):
        try:
            with open(CACHE_FILENAME, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            logging.info(f"Cache caricata con {len(cache)} elementi")
        except Exception as e:
            logging.error(f"Errore nel caricamento della cache: {e}")
            if os.path.exists(CACHE_FILENAME):
                backup_name = f"{CACHE_FILENAME}.bak.{int(time.time())}"
                try:
                    os.rename(CACHE_FILENAME, backup_name)
                    logging.info(f"Backup della cache creato: {backup_name}")
                except Exception as e:
                    logging.error(f"Impossibile creare backup della cache: {e}")
            cache = {}
    else:
        cache = {}

def save_cache():
    global cache_modified
    
    if not cache_modified:
        logging.info("Nessuna modifica alla cache, salvataggio non necessario")
        return
        
    try:
        temp_filename = f"{CACHE_FILENAME}.temp"
        with open(temp_filename, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=4)
        
        if os.path.exists(CACHE_FILENAME):
            os.replace(temp_filename, CACHE_FILENAME)
        else:
            os.rename(temp_filename, CACHE_FILENAME)
            
        logging.info(f"Cache salvata con {len(cache)} elementi")
        cache_modified = False
    except Exception as e:
        logging.error(f"Errore nel salvataggio della cache: {e}")

def save_retry_candidates(retry_candidates):
    try:
        with open(RETRY_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(retry_candidates, f, indent=4)
        logging.info(f"Salvati {len(retry_candidates)} candidati per retry")
    except Exception as e:
        logging.error(f"Errore nel salvataggio dei candidati retry: {e}")

def load_retry_candidates():
    if os.path.exists(RETRY_FILENAME):
        try:
            with open(RETRY_FILENAME, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Errore nel caricamento dei candidati retry: {e}")
    return []

def save_results(results, metadata_collected, not_found_dois, summary, retry_candidates=None):
    try:
        with open(NOT_FOUND_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(not_found_dois, f, indent=4)
        logging.info(f"Salvati {len(not_found_dois)} DOI non trovati")
        
        with open(METADATA_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(metadata_collected, f, indent=4)
        logging.info(f"Salvati metadati per {len(metadata_collected)} DOI")
        
        converted_count = 0
        if metadata_collected:
            converted_data = convert_metadata_format(metadata_collected)
            converted_count = len(converted_data)
            with open(CONVERTED_FILENAME, 'w', encoding='utf-8') as f:
                json.dump(converted_data, f, indent=4)
            logging.info(f"Salvati metadati convertiti per {converted_count} DOI")
        
        with open(SUMMARY_FILENAME, 'w', encoding='utf-8') as f:
            json.dump({
                'summary': summary,
                'timestamp': time.time(),
                'total_results': len(results),
                'converted_papers': converted_count,
                'include_citations': INCLUDE_CITATIONS
            }, f, indent=4)
        logging.info(f"Riepilogo salvato")
        
        if retry_candidates:
            save_retry_candidates(retry_candidates)
        
    except Exception as e:
        logging.error(f"Errore nel salvataggio dei risultati: {e}")
        raise

def query_with_retry(doi: str, max_retries=MAX_RETRIES) -> Tuple[bool, Dict, Optional[str]]:
    """Query META API per i metadati."""
    normalized_doi = normalize_doi(doi)
    if not normalized_doi:
        return False, {}, None
        
    headers = {"authorization": OPENCITATIONS_ACCESS_TOKEN.strip()}
    last_error_type = None
    
    for attempt in range(max_retries):
        try:
            url = f"{OPENCITATION_META_URL}doi:{normalized_doi}"
            logging.debug(f"Richiesta a: {url}")
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 429:
                wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                logging.warning(f"Rate limit. Attesa {wait_time}s")
                last_error_type = f"HTTP_{response.status_code}"
                time.sleep(wait_time)
                continue
                
            if response.status_code in [500, 502, 503, 504]:
                wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                logging.warning(f"Errore {response.status_code}. Attesa {wait_time}s")
                last_error_type = f"HTTP_{response.status_code}"
                time.sleep(wait_time)
                continue
                
            if response.status_code == 404:
                logging.info(f"DOI {normalized_doi} non trovato (404)")
                return False, {}, "HTTP_404"
            
            if not response.text or response.text.strip() == "":
                if attempt < max_retries - 1:
                    wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                    time.sleep(wait_time)
                    continue
                else:
                    return False, {}, "EmptyResponse"
            
            content_type = response.headers.get('content-type', '').lower()
            if 'html' in content_type:
                if attempt == 0:
                    logging.warning(f"API restituisce HTML per DOI {normalized_doi}")
                return False, {}, "HTML_Response_NotFound"
                
            response.raise_for_status()
            
            try:
                json_data = response.json()
                return True, json_data, None
            except json.JSONDecodeError:
                if attempt == max_retries - 1:
                    if response.status_code == 200:
                        return False, {}, "InvalidJSON_NotFound"
                    last_error_type = "JSONDecodeError"
                    break
                else:
                    wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                    time.sleep(wait_time)
                    continue
            
        except requests.exceptions.Timeout:
            wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
            last_error_type = "Timeout"
            time.sleep(wait_time)
            
        except requests.exceptions.ConnectionError:
            wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
            last_error_type = "ConnectionError"
            time.sleep(wait_time)
            
        except requests.exceptions.RequestException as e:
            wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
            last_error_type = str(type(e).__name__)
            time.sleep(wait_time)
    
    logging.error(f"Tutti i tentativi falliti per DOI {normalized_doi}")
    return False, {}, last_error_type

def check_doi_in_opencitation(doi: str, force_refresh=False) -> Tuple[bool, Dict, Optional[str]]:
    global processed_count, cache_modified
    
    normalized_doi = normalize_doi(doi)
    if not normalized_doi:
        return False, {}, None
    
    if not force_refresh and normalized_doi in cache:
        cached_entry = cache[normalized_doi]
        if cached_entry.get("error_type") and is_retryable_error(exception_type=cached_entry.get("error_type")):
            logging.info(f"DOI nella cache con errore retryable, rifacendo la query")
        else:
            logging.debug(f"DOI {normalized_doi} trovato nella cache")
            return cached_entry.get("exists", False), cached_entry.get("metadata", {}), cached_entry.get("error_type")
    
    time.sleep(RATE_LIMIT_DELAY)
    
    exists, metadata, error_type = query_with_retry(normalized_doi)
    
    cache[normalized_doi] = {
        "exists": exists,
        "metadata": metadata if exists else {},
        "error_type": error_type,
        "timestamp": time.time()
    }
    cache_modified = True
    
    return exists, metadata, error_type

def read_input_file():
    try:
        with open(INPUT_FILENAME, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            
            if lines and "key,doi" in lines[0]:
                lines = lines[1:]
                
            doi_entries = []
            for line in lines:
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    doi = normalize_doi(parts[1])
                    if doi:
                        doi_entries.append({
                            "key": parts[0],
                            "doi": doi
                        })
            
            return doi_entries
    except Exception as e:
        logging.error(f"Errore nella lettura del file di input: {e}")
        raise

def process_batch(entries, start_idx, end_idx, test_mode=False, retry_mode=False):
    global processed_count
    
    results = []
    opencitation_success = 0
    opencitation_failure = 0
    not_found_dois = []
    metadata_collected = []
    retry_candidates = []
    
    batch_entries = entries[start_idx:end_idx]
    
    mode_text = "(TEST)" if test_mode else "(RETRY)" if retry_mode else ""
    if INCLUDE_CITATIONS:
        mode_text += " [CON CITAZIONI]"
    logging.info(f"Elaborazione batch {start_idx+1}-{end_idx} {mode_text}")
    
    for i, entry in enumerate(batch_entries):
        key = entry.get('key')
        doi = entry.get('doi')
        
        current_global_idx = start_idx + i + 1
        
        if current_global_idx % 20 == 0 or current_global_idx == end_idx:
            logging.info(f"Progresso: {current_global_idx}/{end_idx} - OK: {opencitation_success}, KO: {opencitation_failure}")
        
        try:
            exists, metadata, error_type = check_doi_in_opencitation(doi, force_refresh=retry_mode)
            processed_count += 1
            
            if exists:
                result_metadata = {'key': key, 'doi': doi, 'metadata': metadata}
                
                if INCLUDE_CITATIONS:
                    logging.info(f"Recupero citazioni per DOI {doi}")
                    
                    ref_success, outgoing_citations, ref_error = get_references(doi)
                    if ref_success:
                        result_metadata['outgoing_citations'] = outgoing_citations
                        logging.info(f"  - References: {len(outgoing_citations)}")
                    else:
                        result_metadata['outgoing_citations'] = []
                    
                    cit_success, incoming_citations, cit_error = get_citations(doi)
                    if cit_success:
                        result_metadata['incoming_citations'] = incoming_citations
                        logging.info(f"  - Citations: {len(incoming_citations)}")
                    else:
                        result_metadata['incoming_citations'] = []
                
                opencitation_success += 1
                results.append({'key': key, 'doi': doi, 'status': 'found'})
                metadata_collected.append(result_metadata)
            else:
                opencitation_failure += 1
                result_entry = {'key': key, 'doi': doi, 'status': 'not found'}
                
                if error_type and is_retryable_error(exception_type=error_type):
                    retry_candidates.append({
                        'key': key, 
                        'doi': doi, 
                        'error_type': error_type,
                        'timestamp': time.time()
                    })
                    result_entry['retryable'] = True
                else:
                    result_entry['retryable'] = False
                
                results.append(result_entry)
                not_found_dois.append({'key': key, 'doi': doi, 'error_type': error_type})
                
            if processed_count % CACHE_SAVE_INTERVAL == 0:
                save_cache()
                
        except Exception as e:
            logging.error(f"Errore elaborazione DOI {doi}: {e}")
            opencitation_failure += 1
            
            is_retryable = is_retryable_error(exception_type=str(e))
            
            if is_retryable:
                retry_candidates.append({
                    'key': key, 
                    'doi': doi, 
                    'error_type': str(e),
                    'timestamp': time.time()
                })
            
            results.append({
                'key': key, 
                'doi': doi, 
                'status': 'error', 
                'error': str(e),
                'retryable': is_retryable
            })
            not_found_dois.append({'key': key, 'doi': doi, 'error_type': str(e)})
    
    return results, metadata_collected, not_found_dois, opencitation_success, opencitation_failure, retry_candidates

def process_retry_batch():
    retry_candidates = load_retry_candidates()
    
    if not retry_candidates:
        logging.info("Nessun candidato per retry trovato.")
        return None, None, None, 0, 0, []
    
    logging.info(f"Trovati {len(retry_candidates)} candidati per retry.")
    
    retry_entries = []
    for candidate in retry_candidates:
        retry_entries.append({
            "key": candidate['key'],
            "doi": candidate['doi']
        })
    
    return process_batch(retry_entries, 0, len(retry_entries), retry_mode=True)

def main():
    """Funzione principale che gestisce l'intero processo."""
    global processed_count, cache_modified
    
    select_mode()
    setup_output_paths()
    setup_logging()
    
    try:
        load_cache()
        processed_count = 0
        cache_modified = False
        
        retry_candidates = load_retry_candidates()
        if retry_candidates:
            print(f"\nTrovati {len(retry_candidates)} DOI candidati per retry.")
            retry_choice = input("Vuoi processare prima i retry? (s/n): ").strip().lower()
            
            if retry_choice in ['s', 'si', 'y', 'yes']:
                logging.info("="*60)
                logging.info("FASE RETRY: RIPROCESSAMENTO DOI FALLITI")
                logging.info("="*60)
                
                retry_results, retry_metadata, retry_not_found, retry_success, retry_failure, new_retry_candidates = process_retry_batch()
                
                if retry_results:
                    save_cache()
                    
                    retry_summary = {
                        'total_dois': len(retry_candidates),
                        'opencitation_success': retry_success,
                        'opencitation_failure': retry_failure,
                        'success_percentage': round((retry_success / len(retry_candidates)) * 100, 2) if retry_candidates else 0,
                        'new_retry_candidates': len(new_retry_candidates)
                    }
                    
                    save_results(retry_results, retry_metadata, retry_not_found, retry_summary, new_retry_candidates)
                    
                    logging.info("="*60)
                    logging.info("RISULTATI DEL RETRY:")
                    logging.info(f"DOI ritentati: {len(retry_candidates)}")
                    logging.info(f"Nuovi successi: {retry_success}")
                    logging.info(f"Ancora falliti: {retry_failure}")
                    logging.info(f"Percentuale successo: {retry_summary['success_percentage']}%")
                    logging.info("="*60)
        
        entries = read_input_file()
        if not entries:
            logging.error("Nessun DOI trovato nel file di input")
            return
        
        logging.info(f"Totale DOI da elaborare: {len(entries)}")
        logging.info(f"Risultati salvati in: {OUTPUT_DIR}")
        if INCLUDE_CITATIONS:
            logging.info("MODALITÀ: Con recupero citazioni (in entrata e in uscita)")
        else:
            logging.info("MODALITÀ: Solo metadati standard")
        
        logging.info("="*60)
        logging.info("FASE 1: TEST CON I PRIMI 100 DOI")
        logging.info("="*60)
        
        test_end = min(TEST_BATCH_SIZE, len(entries))
        test_results, test_metadata, test_not_found, test_success, test_failure, test_retry_candidates = process_batch(
            entries, 0, test_end, test_mode=True
        )
        
        save_cache()
        
        test_summary = {
            'total_dois': test_end,
            'opencitation_success': test_success,
            'opencitation_failure': test_failure,
            'success_percentage': round((test_success / test_end) * 100, 2) if test_end > 0 else 0,
            'retry_candidates': len(test_retry_candidates)
        }
        
        save_results(test_results, test_metadata, test_not_found, test_summary, test_retry_candidates)
        
        logging.info("="*60)
        logging.info("RISULTATI DEL TEST:")
        logging.info(f"DOI elaborati: {test_end}")
        logging.info(f"Successi: {test_success}")
        logging.info(f"Fallimenti: {test_failure}")
        logging.info(f"Percentuale successo: {test_summary['success_percentage']}%")
        logging.info("="*60)
        
        print("\n" + "="*60)
        print("TEST COMPLETATO!")
        print(f"Elaborati i primi {test_end} DOI.")
        print(f"Controlla i file in: {OUTPUT_DIR}")
        print(f"- {os.path.basename(NOT_FOUND_FILENAME)}")
        print(f"- {os.path.basename(METADATA_FILENAME)}")
        print(f"- {os.path.basename(CONVERTED_FILENAME)}")
        if INCLUDE_CITATIONS:
            print("  (include citazioni in entrata e in uscita)")
        print(f"- {os.path.basename(SUMMARY_FILENAME)}")
        print(f"- {os.path.basename(CACHE_FILENAME)}")
        if test_retry_candidates:
            print(f"- {os.path.basename(RETRY_FILENAME)} ({len(test_retry_candidates)} candidati)")
        print("="*60)
        
        if len(entries) > TEST_BATCH_SIZE:
            choice = input(f"\nContinuare con i rimanenti {len(entries) - TEST_BATCH_SIZE} DOI? (s/n): ").strip().lower()
            
            if choice in ['s', 'si', 'y', 'yes']:
                logging.info("="*60)
                logging.info("FASE 2: ELABORAZIONE COMPLETA")
                logging.info("="*60)
                
                remaining_results, remaining_metadata, remaining_not_found, remaining_success, remaining_failure, remaining_retry_candidates = process_batch(
                    entries, TEST_BATCH_SIZE, len(entries)
                )
                
                all_results = test_results + remaining_results
                all_metadata = test_metadata + remaining_metadata
                all_not_found = test_not_found + remaining_not_found
                all_retry_candidates = test_retry_candidates + remaining_retry_candidates
                total_success = test_success + remaining_success
                total_failure = test_failure + remaining_failure
                
                final_summary = {
                    'total_dois': len(entries),
                    'opencitation_success': total_success,
                    'opencitation_failure': total_failure,
                    'success_percentage': round((total_success / len(entries)) * 100, 2) if entries else 0,
                    'retry_candidates': len(all_retry_candidates)
                }
                
                save_cache()
                save_results(all_results, all_metadata, all_not_found, final_summary, all_retry_candidates)
                
                logging.info("="*60)
                logging.info("ELABORAZIONE COMPLETATA!")
                logging.info(f"Totale DOI elaborati: {len(entries)}")
                logging.info(f"Successi: {total_success}")
                logging.info(f"Fallimenti: {total_failure}")
                logging.info(f"Percentuale successo: {final_summary['success_percentage']}%")
                logging.info(f"File salvati in: {OUTPUT_DIR}")
                logging.info("="*60)
                
                if all_retry_candidates:
                    print(f"\nCi sono {len(all_retry_candidates)} DOI candidati per retry.")
                    print("Puoi rieseguire lo script per riprovare automaticamente.")
                    
            else:
                logging.info("Elaborazione interrotta dopo il test.")
        else:
            logging.info("Test completato. Tutti i DOI elaborati.")
            if test_retry_candidates:
                print(f"\nCi sono {len(test_retry_candidates)} DOI candidati per retry.")
                print("Puoi rieseguire lo script per riprovare.")
    
    except FileNotFoundError as e:
        logging.error(f"File {INPUT_FILENAME} non trovato: {e}")
    except json.JSONDecodeError as e:
        logging.error(f"Errore decodifica JSON: {e}")
    except Exception as e:
        logging.exception("Errore imprevisto")
        save_cache()

if __name__ == '__main__':
    try:
        print("="*60)
        print("OPENCITATIONS METADATA RETRIEVAL v2")
        print("="*60)
        main()
        print("\nProgramma terminato.")
    except Exception as e:
        print(f"\n!!! ERRORE CRITICO !!!")
        print(f"Tipo: {type(e).__name__}")
        print(f"Messaggio: {e}")
        import traceback
        traceback.print_exc()
        input("\nPremi INVIO per chiudere...")