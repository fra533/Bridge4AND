'''
step5_build_author_centric.py

Questo modulo gestisce la conversione di metadati bibliografici da un formato 
orientato alle pubblicazioni (sna_valid_pub) a un formato orientato agli autori 
(sna_valid_raw). Il sistema normalizza i nomi degli autori per garantire coerenza 
e compatibilità con i file system.

Regole di Normalizzazione:
    1. Conversione in lowercase
    2. Rimozione abbreviazioni (es. "J." → "j")
    3. Rimozione caratteri speciali
    4. Estrazione primo nome e cognome
    5. Formato finale: primo_nome_cognome
    6. Limite lunghezza: 100 caratteri

    '''


import json
from collections import defaultdict
import re

def normalize_author_name(name):
    """
    Normalizza il nome dell'autore convertendolo in lowercase 
    e sostituendo spazi con underscore, rimuovendo caratteri speciali.
    Garantisce che il risultato abbia SEMPRE e SOLO un underscore (primo_nome_cognome).
    """
    normalized = name.strip().lower()
    
    normalized = re.sub(r'\b([a-z])\.\s*', r'\1 ', normalized)
    
    normalized = re.sub(r'\.', '', normalized)
    
    normalized = re.sub(r"[^\w\s\-]", "", normalized)
    
    normalized = re.sub(r'\s*-\s*', '-', normalized)
    
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    parts = normalized.split()
    
    if len(parts) >= 2:
        first_name = parts[0]
        last_name = parts[-1]  
        normalized = f"{first_name}_{last_name}"
    elif len(parts) == 1:
        normalized = f"unknown_{parts[0]}"
    else:
        normalized = "unknown_unknown"
    
    normalized = re.sub(r'_+', '_', normalized)
    
    normalized = normalized.strip('_')
    
    if len(normalized) > 100:
        normalized = normalized[:100].rstrip('_')
    
    return normalized

def build_sna_valid_raw(sna_valid_pub):
    """
    Costruisce sna_valid_raw a partire da sna_valid_pub
    
    Args:
        sna_valid_pub (dict): Dizionario con metadati delle pubblicazioni
        
    Returns:
        dict: Dizionario con autori come chiavi e liste di ID pubblicazioni come valori
    """
    author_publications = defaultdict(list)
    
    # Itera su tutte le pubblicazioni
    for pub_id, publication in sna_valid_pub.items():
        # Controlla se la pubblicazione ha autori
        if 'authors' in publication and publication['authors']:
            # Per ogni autore della pubblicazione
            for author in publication['authors']:
                # Usa solo il nome dell'autore, ignora l'organizzazione
                if 'name' in author and author['name']:
                    # Normalizza il nome dell'autore
                    normalized_name = normalize_author_name(author['name'])
                    
                    # Aggiungi solo se il nome normalizzato non è vuoto
                    if normalized_name:
                        # Aggiungi l'ID della pubblicazione alla lista dell'autore
                        author_publications[normalized_name].append(pub_id)
                else:
                    print(f"Attenzione: Autore senza nome nella pubblicazione {pub_id}")
    
    # Converte defaultdict in dict normale
    return dict(author_publications)

def load_and_convert(input_file_path, output_file_path=r"C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BondforOC\results\test_metadataOC_with_citations_raw.json"):
    """
    Carica sna_valid_pub da file JSON e salva sna_valid_raw
    
    Args:
        input_file_path (str): Percorso del file sna_valid_pub.json
        output_file_path (str): Percorso dove salvare sna_valid_raw.json (default: results/converted_metadata_raw.json)
    """
    try:
        import os
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        
        with open(input_file_path, 'r', encoding='utf-8') as f:
            sna_valid_pub = json.load(f)
        
        print(f"Caricato file con {len(sna_valid_pub)} pubblicazioni")
        
        sna_valid_raw = build_sna_valid_raw(sna_valid_pub)
        
        print(f"Trovati {len(sna_valid_raw)} autori unici")
        
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump(sna_valid_raw, f, indent=2, ensure_ascii=False)
        
        print(f"File sna_valid_raw salvato in: {output_file_path}")
        
        total_publications = sum(len(pubs) for pubs in sna_valid_raw.values())
        avg_pubs_per_author = total_publications / len(sna_valid_raw) if sna_valid_raw else 0
        
        print(f"\nStatistiche:")
        print(f"- Autori totali: {len(sna_valid_raw)}")
        print(f"- Pubblicazioni totali (con possibili duplicati): {total_publications}")
        print(f"- Media pubblicazioni per autore: {avg_pubs_per_author:.2f}")
        
        return sna_valid_raw
        
    except FileNotFoundError:
        print(f"Errore: File {input_file_path} non trovato")
        return None
    except json.JSONDecodeError:
        print(f"Errore: File {input_file_path} non è un JSON valido")
        return None
    except Exception as e:
        print(f"Errore durante l'elaborazione: {e}")
        return None

if __name__ == "__main__":

    # Esempio 
    sna_valid_pub_example = {
        "rJZe1IHB": {
            "id": "rJZe1IHB",
            "title": "Enhanced Terrestrial Carbon Uptake in the Northern High Latitudes in the 21st Century from the C4MIP Model Projections",
            "abstract": "",
            "keywords": [
                "carbon-climate",
                "terrestrial carbon"
            ],
            "authors": [
                {
                    "name": "Haifeng Qian",
                    "org": ""
                },
                {
                    "name": "Renu Joseph",
                    "org": ""
                },
                {
                    "name": "Ning Zeng",
                    "org": ""
                }
            ],
            "venue": "",
            "year": 2008
        },
        "LNZGIKx7": {
            "id": "LNZGIKx7",
            "title": "Another Paper",
            "abstract": "",
            "keywords": [],
            "authors": [
                {
                    "name": "Haifeng Qian",
                    "org": ""
                },
                {
                    "name": "John Doe",
                    "org": ""
                }
            ],
            "venue": "",
            "year": 2009
        }
    }
    
    print("=== Test con dati di esempio ===")
    result = build_sna_valid_raw(sna_valid_pub_example)
    print("Risultato:")
    for author, publications in result.items():
        print(f"  {author}: {publications}")
    
    
    print("\n=== Conversione del file ===")
    load_and_convert(r'C:\Users\franc\OneDrive - Alma Mater Studiorum Università di Bologna\Desktop\BondforOC\results\OC_train_results_with_citations\converted_metadata.json')