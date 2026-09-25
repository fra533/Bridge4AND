## Struttura della pipeline

Una limitazione strutturale fondamentale del benchmark WhoIsWho è la totale assenza di identificatori persistenti digitali (DOI). Per superare tale criticità, abbiamo implementato una procedura di verifica manuale sistematica su un campione di $N = 500$ pubblicazioni, estratto dai sottoinsiemi ad elevata ambiguità della partizione di validazione di WhoIsWho.

Per ciascun record, i revisori hanno interrogato direttamente il portale AMiner per gestire traduzioni strutturali o normalizzazioni anglicizzate dei titoli non occidentali. Qualora il DOI non fosse presente su AMiner, sono state eseguite ricerche secondarie sui portali aperti degli editori. I record per i quali non è stato possibile recuperare alcun identificatore persistente verificato sono stati contrassegnati come \texttt{None}. Il set curato risultante ($\mathrm{DOI}_{\mathrm{gold}}$) è stato formalizzato in uno schema tabellare verificabile (\texttt{gold_standard.csv}), impiegato come baseline di calibrazione.


### 1. `step1_prepare_gold_standard.py`

**Scopo**: Prepara il gold standard verificando i DOI su Crossref e dividendo in training/validation.

**Input**:
- `gold_standard.csv` - CSV con colonne: `Key`, `title`, `DOI`, `Cinese_title`

**Output**:
- `results/training_set.csv` - 300 esempi per training
- `results/validation_set.csv` - Rimanenti esempi per validazione
- `results/failed_requests.csv` - DOI con errori API

**Parametri Configurabili**:
```python
MAX_RETRIES = 3          # Tentativi per ogni richiesta
RETRY_DELAY = 5          # Secondi tra i retry
training_size = 300      # Dimensione training set
```

**Esecuzione**:
```bash
python step1_prepare_gold_standard.py
```

---

### 2. `step2_calibrate_crossref_cutoff.py`

**Scopo**: Analizza il training set per trovare il cutoff ottimale di Crossref score.

**Input**:
- `data/Bondvalidation.json` - Metadati paper in formato JSON
- `results/training_set.csv` - Training set preparato
- `results/validation_set.csv` - Validation set

**Output**:
- `results/crossref_score_analysis.png` - Grafico scatter plot
- `results/crossref_cutoff_analysis.csv` - Metriche per ogni cutoff
- `results/validation_results.csv` - Risultati validation set
- `results/crossref_training_cache.json` - Cache query Crossref
- `results/wrong_matches_analysis.csv` - Analisi errori

**Caratteristiche**:
-  Sistema di caching intelligente
-  Validazione con Levenshtein distance (similarità titoli)
-  Verifica esatta dell'anno
-  Calcolo automatico del cutoff ottimale

**Esecuzione**:
```bash
# Con cutoff automatico
python step2_calibrate_crossref_cutoff.py

# Con cutoff manuale
# Modifica nel file: main(manual_cutoff=35.0)
```

**Metriche Calcolate**:
- Accuracy
- Precision
- Recall
- F1 Score

---

### 3. `step3_validate_crossref_dois.py`

**Scopo**: Validazione massiva del dataset completo con multiprocessing.

**Input**:
- `data/Bondvalidation.json` - Dataset completo
- Cutoff ottimale da fase 2

**Output**:
- `results/Bond_crossref_validated/validated_keys_dois.csv` - Paper validati con DOI
- `results/Bond_crossref_validated/rejected_items.csv` - Paper rifiutati
- `results/Bond_crossref_validated/error_items.csv` - Paper con errori
- `results/crossref_cache.json` - Cache globale

**Parametri Configurabili**:
```python
manual_cutoff = 35.0      # Cutoff Crossref score
num_processes = 4         # Processi paralleli
use_cache = True          # Usa cache
```

**Caratteristiche**:
- Multiprocessing per velocizzare l'elaborazione
- Cache condivisa tra processi
- Validazione metadati (titolo + anno)

**Esecuzione**:
```bash
python step3_validate_crossref_dois.py
```

---

### 4. `step4_enrich_opencitations.py` 

**Scopo**: Recupera metadati bibliografici e citazioni da OpenCitations (API v2).

**Input**:
- `validated_keys_dois.csv` - Paper validati con DOI

**Output**:

**Modalità 1 - Standard** (`OC_results/`):
- `converted_metadata.json` - Metadati formato target
- `opencitations_metadata.json` - Metadati formato originale
- `final_batch_notfound.json` - DOI non trovati
- `processing_summary.json` - Statistiche
- `opencitations_cache.json` - Cache
- `opencitations_app.log` - Log dettagliato

**Modalità 2 - Con Citazioni** (`OC_results_with_citations/`):
- Stesso output della Modalità 1 +
- `outgoing_citations`: Lista DOI citati da questo paper
- `incoming_citations`: Lista DOI che citano questo paper
- `*_count`: Conteggi citazioni

**Formato Output Convertito**:
```json
{
  "paper_key": {
    "id": "paper_key",
    "title": "Paper Title",
    "abstract": "",
    "keywords": ["keyword1", "keyword2"],
    "authors": [
      {"name": "Author Name", "org": ""}
    ],
    "venue": "Journal Name",
    "year": 2020,
    "outgoing_citations": ["10.1234/doi1"],
    "incoming_citations": ["10.5678/doi2"],
    "outgoing_citations_count": 1,
    "incoming_citations_count": 1
  }
}
```

**Caratteristiche**:
- API OpenCitations v2 (META + INDEX)
- Due modalità di esecuzione
- Fase test con primi 100 DOI
- Caching 
- Gestione rate limiting
- Statistiche dettagliate

**Esecuzione**:
```bash
python step4_enrich_opencitations.py

```

**Fasi di Esecuzione**:
1. **Selezione modalità** - Scegli se includere citazioni
2. **Test batch** - Elabora primi 100 DOI
3. **Verifica risultati** - Controlla file output
4. **Elaborazione completa** - Processa tutti i DOI (opzionale)
5. **Retry** - Riprova DOI falliti nelle esecuzioni successive


**API Endpoint Utilizzati e proprietà**:
```
META API:   https://api.opencitations.net/meta/v1/metadata/doi:{DOI}
INDEX API:  https://api.opencitations.net/index/v2/citations/doi:{DOI}
            https://api.opencitations.net/index/v2/references/doi:{DOI}
```

---

### 5. `step5_build_author_centric.py`

**Scopo**: Converte metadati da formato paper-centrico a formato autore-centrico.

**Input**:
- `results/OC_results/converted_metadata.json`

**Output**:
- `results/converted_metadata_raw.json` - Formato autore → paper

**Formato Output**:
```json
{
  "mario_rossi": ["paper1", "paper2"],
  "john_doe": ["paper3"]
}
```

**Normalizzazione Nomi**:
- Lowercase
- Rimozione abbreviazioni (J. → j)
- Rimozione caratteri speciali
- Formato: `primo_nome_cognome`
- Limite: 100 caratteri

**Esecuzione**:
```bash
python step5_build_author_centric.py
```

---
