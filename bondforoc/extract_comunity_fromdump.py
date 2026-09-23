#!/usr/bin/env python3
"""
Script per estrarre dati da OpenCitations Meta e COCI
Legge CSV direttamente da file .tar.gz (senza ZIP interni)
Supporta Windows/Linux
Filtra per ISSN multipli
Include pubblicazioni con almeno un autore con ORCID
Mantiene SOLO gli autori con ORCID (esclude autori senza ORCID)
CON SISTEMA DI CHECKPOINT PER RIPRESA AUTOMATICA

"""

import os
import csv
import json
import re
import tarfile
import random
import string
import pickle
from io import TextIOWrapper
from collections import defaultdict
from pathlib import Path


def parse_oc_csv_line(line):
    """
    Parse una riga del CSV OpenCitations con formato speciale
    Il CSV ha tutto racchiuso in virgolette con punti e virgola finali
    """
    # Rimuovi caratteri problematici
    line = line.rstrip('\r\n;')

    # Rimuovi virgolette esterne
    if line.startswith('"') and line.endswith('"'):
        line = line[1:-1]

    # Sostituisci virgolette doppie
    line = line.replace('""', '"')

    # Split intelligente rispettando virgolette e parentesi
    fields = []
    current_field = ""
    in_quotes = False
    in_brackets = 0

    for char in line:
        if char == '"':
            in_quotes = not in_quotes
            current_field += char
        elif char == '[':
            in_brackets += 1
            current_field += char
        elif char == ']':
            in_brackets -= 1
            current_field += char
        elif char == ',' and not in_quotes and in_brackets == 0:
            fields.append(current_field.strip().strip('"'))
            current_field = ""
        else:
            current_field += char

    if current_field:
        fields.append(current_field.strip().strip('"'))

    return fields


class OpenCitationsExtractor:
    def __init__(self, meta_path, coci_path, target_issn=None, checkpoint_dir="checkpoints"):
        self.meta_path = meta_path
        self.coci_path = coci_path
        # Converti in lista se è una stringa singola
        if isinstance(target_issn, str):
            target_issn = [target_issn]
        self.target_issn = target_issn or ["1588-2861"]
        self.publications = {}
        self.citations = defaultdict(lambda: {"incoming": [], "outgoing": []})
        self.omid_to_short_id = {}
        
        # ========== CHECKPOINT SYSTEM ==========
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)
        
        # File checkpoint
        self.checkpoint_meta = self.checkpoint_dir / "checkpoint_meta.json"
        self.checkpoint_data = self.checkpoint_dir / "checkpoint_data.pkl"
        
        # Stato del processing
        self.processed_csvs = set()  # CSV già processati
        self.meta_completed = False
        self.coci_completed = False
        
        # Carica checkpoint se esiste
        self.load_checkpoint()

    def save_checkpoint(self, phase="meta"):
        """Salva checkpoint dei dati processati"""
        try:
            # Salva metadati checkpoint
            checkpoint_info = {
                "processed_csvs": list(self.processed_csvs),
                "meta_completed": self.meta_completed,
                "coci_completed": self.coci_completed,
                "num_publications": len(self.publications),
                "num_citations": len(self.citations),
                "target_issn": self.target_issn,
                "phase": phase
            }
            
            with open(self.checkpoint_meta, 'w') as f:
                json.dump(checkpoint_info, f, indent=2)
            
            # Salva dati (pickle per preservare defaultdict)
            checkpoint_data = {
                "publications": dict(self.publications),
                "citations": dict(self.citations),
                "omid_to_short_id": self.omid_to_short_id
            }
            
            with open(self.checkpoint_data, 'wb') as f:
                pickle.dump(checkpoint_data, f)
            
            print(f"\n💾 Checkpoint salvato: {len(self.publications)} pubblicazioni")
            
        except Exception as e:
            print(f"\n⚠️  Errore salvando checkpoint: {e}")
    
    def load_checkpoint(self):
        """Carica checkpoint se esiste"""
        if not self.checkpoint_meta.exists() or not self.checkpoint_data.exists():
            return
        
        try:
            # Carica metadati
            with open(self.checkpoint_meta, 'r') as f:
                checkpoint_info = json.load(f)
            
            # Verifica che gli ISSN target siano gli stessi
            if checkpoint_info.get("target_issn") != self.target_issn:
                print("\n⚠️  ISSN target diversi dal checkpoint, ricomincio da zero...")
                return
            
            # Carica dati
            with open(self.checkpoint_data, 'rb') as f:
                checkpoint_data = pickle.load(f)
            
            self.publications = checkpoint_data["publications"]
            self.citations = defaultdict(
                lambda: {"incoming": [], "outgoing": []},
                checkpoint_data["citations"]
            )
            self.omid_to_short_id = checkpoint_data["omid_to_short_id"]
            
            self.processed_csvs = set(checkpoint_info["processed_csvs"])
            self.meta_completed = checkpoint_info["meta_completed"]
            self.coci_completed = checkpoint_info["coci_completed"]
            
            print("\n" + "="*70)
            print("📂 CHECKPOINT TROVATO - RIPRESA DA DOVE INTERROTTO")
            print("="*70)
            print(f"Pubblicazioni caricate: {len(self.publications)}")
            print(f"CSV già processati: {len(self.processed_csvs)}")
            print(f"Fase META completata: {'✅' if self.meta_completed else '❌'}")
            print(f"Fase COCI completata: {'✅' if self.coci_completed else '❌'}")
            print("="*70)
            
        except Exception as e:
            print(f"\n⚠️  Errore caricando checkpoint: {e}")
            print("Ricomincio da zero...")
            self.processed_csvs = set()
            self.meta_completed = False
            self.coci_completed = False
    
    def clear_checkpoint(self):
        """Cancella i file di checkpoint"""
        try:
            if self.checkpoint_meta.exists():
                self.checkpoint_meta.unlink()
            if self.checkpoint_data.exists():
                self.checkpoint_data.unlink()
            print("\n🗑️  Checkpoint cancellato")
        except Exception as e:
            print(f"\n⚠️  Errore cancellando checkpoint: {e}")

    def extract_orcid_from_author(self, author_string):
        """
        Estrae ORCID da una stringa autore.
        Formato atteso: "Surname, Name [omid:ra/... orcid:0000-0002-1234-5678]"
        """
        # Cerca orcid: seguito dal pattern, anche se ci sono altri identificatori
        orcid_pattern = r'orcid:(\d{4}-\d{4}-\d{4}-\d{3}[0-9X])'
        match = re.search(orcid_pattern, author_string)
        if match:
            return match.group(1)
        return None

    def extract_name_from_author(self, author_string):
        """
        Estrae il nome dell'autore rimuovendo ORCID e altri identificatori.
        """
        # Rimuovi ORCID e altri identificatori tra []
        name = re.sub(r'\[[^\]]+\]', '', author_string)
        # Rimuovi spazi extra
        name = name.strip()
        return name

    def generate_short_id(self, length=8):
        """
        Genera un ID corto random stile 'nVZXR80K'
        """
        chars = string.ascii_letters + string.digits
        while True:
            short_id = ''.join(random.choices(chars, k=length))
            # Assicurati che sia unico
            if short_id not in self.omid_to_short_id.values():
                return short_id

    def map_citation_to_short_id(self, omid):
        """
        Converte un OMID in short ID se esiste nel mapping, altrimenti restituisce l'OMID
        """
        return self.omid_to_short_id.get(omid, omid)

    def parse_authors(self, author_field):
        """
        Parse il campo author che può contenere multipli autori separati da ';'
        Mantiene SOLO gli autori con ORCID
        """
        if not author_field:
            return []

        authors = []
        author_list = author_field.split(';')

        for author_str in author_list:
            author_str = author_str.strip()
            orcid = self.extract_orcid_from_author(author_str)

            # ⚠️ FILTRO: Mantieni SOLO se ha ORCID
            if orcid:
                name = self.extract_name_from_author(author_str)
                authors.append({
                    "name": name,
                    "orcid": orcid,
                    "org": ""
                })

        return authors

    def extract_keywords_from_title(self, title):
        """
        Estrae keywords dal titolo
        """
        if not title:
            return []
        words = re.findall(r'\b\w+\b', title.lower())
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from'}
        keywords = [w for w in words if w not in stopwords and len(w) > 2]
        return keywords

    def check_venue_issn(self, venue_field):
        """Verifica se il venue contiene uno degli ISSN target, i nomi delle riviste, o le conferenze target"""
    
        if not venue_field:
            return False

        venue_lower = venue_field.lower()

        # Dizionario con ISSN e nomi alternativi per ogni rivista
        journal_mapping = {
            "1570-8268": [
                "1875-7634",  # ISSN elettronico alternativo
                "journal of web semantics",
                "jws",
                "web semantics"
            ],
            "1570-0844": [
                "2210-4968",  # ISSN elettronico alternativo
                "semantic web journal",
                "swj"
            ],
            "1936-6442": [
                "1936-6450",  # ISSN elettronico alternativo
                "journal of semantic web and information systems",
                "jswis"
            ],
            "2332-7790": [
                "2332-7804",  # ISSN elettronico alternativo
                "transactions on graph data and knowledge",
                "tgdk"
            ],
            "1861-2032": [
                "1861-2040",  # ISSN elettronico alternativo
                "journal on data semantics",
                "jods"
            ],
            "0169-023X": [
                "1872-6933",  # ISSN elettronico alternativo
                "data & knowledge engineering",
                "data and knowledge engineering",
                "dke"
            ],
            "1570-5838": [
                "1875-8533",  # ISSN elettronico alternativo
                "applied ontology"
            ],
            "2451-8484": [
                "2451-8492",  # ISSN elettronico alternativo
                "data science"
            ]
        }

        # ========== NUOVA SEZIONE: CONFERENZE ==========
        # Lista di nomi/sigle delle conferenze da includere
        target_conferences = [
            "international semantic web conference",
            "iswc",  # sigla
            "extended semantic web conference",
            "eswc",  # sigla
            "semantics",  # nome conferenza
            "international conference on knowledge engineering and knowledge management",
            "ekaw",  # sigla
            "international conference on knowledge capture",
            "k-cap",
            "kcap"  # sigla alternativa
        ]

        # Controlla se il venue contiene il nome di una conferenza
        for conf_name in target_conferences:
            if conf_name in venue_lower:
                return True
        # ================================================

        # Controlla se uno degli ISSN target è presente (JOURNAL)
        for target_issn in self.target_issn:
            # Controlla ISSN principale
            if target_issn in venue_field:
                return True

            # Controlla varianti e nomi alternativi
            if target_issn in journal_mapping:
                for variant in journal_mapping[target_issn]:
                    if variant in venue_lower:
                        return True

        return False

    def process_csv_file(self, csv_file, csv_name, data_type="meta"):
        """
        Processa un singolo CSV file
        META: formato speciale con virgolette
        COCI: formato CSV standard
        """
        rows_processed = 0
        matches_found = 0
        
        try:
            text_file = TextIOWrapper(csv_file, encoding='utf-8')
            
            # ========== COCI: CSV STANDARD ==========
            if data_type == "coci":
                # COCI ha formato CSV standard: oci,citing,cited,creation,timespan,journal_sc,author_sc
                reader = csv.DictReader(text_file)
                pub_ids = set(self.publications.keys())
                
                for row in reader:
                    rows_processed += 1
                    
                    if rows_processed % 50000 == 0:
                        print(f"      Righe: {rows_processed:,}, Citazioni: {matches_found}", end='\r')
                    
                    citing = row.get('citing', '').strip()
                    cited = row.get('cited', '').strip()
                    
                    # Aggiungi citazione se il citing è nelle nostre pubblicazioni
                    if citing in pub_ids:
                        self.citations[citing]["outgoing"].append(cited)
                        matches_found += 1
                    
                    # Aggiungi citazione se il cited è nelle nostre pubblicazioni
                    if cited in pub_ids:
                        self.citations[cited]["incoming"].append(citing)
                        # Non contare due volte se sia citing che cited sono nelle pub
                        if citing not in pub_ids:
                            matches_found += 1
                
                print(f"      Righe: {rows_processed:,}, Citazioni: {matches_found}     ")
                return rows_processed, matches_found
            
            # ========== META: FORMATO SPECIALE ==========
            # Leggi header
            header_line = text_file.readline()
            headers = parse_oc_csv_line(header_line)
            
            # Trova indici colonne per META
            try:
                id_idx = headers.index('id')
                title_idx = headers.index('title')
                author_idx = headers.index('author')
                date_idx = headers.index('pub_date')
                venue_idx = headers.index('venue')
            except ValueError as e:
                print(f"\n      ⚠️  Header malformato in META: {e}")
                print(f"      Headers trovati: {headers[:10] if len(headers) > 10 else headers}")
                return 0, 0
            
            # Processa righe META
            for line in text_file:
                rows_processed += 1
                
                if rows_processed % 10000 == 0:
                    print(f"      Righe: {rows_processed:,}, Pubblicazioni: {matches_found}", end='\r')
                
                # Parse riga con formato speciale
                fields = parse_oc_csv_line(line)
                if len(fields) < max(id_idx, title_idx, author_idx, date_idx, venue_idx) + 1:
                    continue
                
                # Controlla venue/ISSN
                venue = fields[venue_idx] if venue_idx < len(fields) else ''
                if not self.check_venue_issn(venue):
                    continue
                
                # Parse autori (solo quelli con ORCID)
                author_field = fields[author_idx] if author_idx < len(fields) else ''
                authors = self.parse_authors(author_field)
                
                if not authors:  # Salta se nessun autore con ORCID
                    continue
                
                # Estrai ID pubblicazione
                pub_id_full = fields[id_idx] if id_idx < len(fields) else ''
                pub_id = pub_id_full.split()[0] if pub_id_full else ''
                
                # Estrai altri campi
                title = fields[title_idx] if title_idx < len(fields) else ''
                pub_date = fields[date_idx] if date_idx < len(fields) else ''
                year = pub_date[:4] if pub_date else ''
                
                # Aggiungi pubblicazione
                self.publications[pub_id] = {
                    "id": pub_id,
                    "title": title,
                    "abstract": "",
                    "keywords": self.extract_keywords_from_title(title),
                    "authors": authors,
                    "venue": venue,
                    "year": int(year) if year.isdigit() else None,
                    "outgoing_citations": [],
                    "incoming_citations": []
                }
                matches_found += 1
            
            print(f"      Righe: {rows_processed:,}, Pubblicazioni: {matches_found}     ")
        
        except Exception as e:
            print(f"\n      ERRORE processando {csv_name}: {e}")
            import traceback
            traceback.print_exc()
        
        return rows_processed, matches_found

    def process_tar_gz(self, tar_path, data_type="meta"):
        """
        Processa un file .tar.gz (META) o .zip con ZIP interni (COCI)
        """
        print(f"\n{'='*70}")
        print(f"Processando {data_type.upper()}: {os.path.basename(tar_path)}")
        print(f"{'='*70}")

        if not os.path.exists(tar_path):
            print(f"❌ ERRORE: File non trovato: {tar_path}")
            return

        # Determina se è tar.gz o zip
        is_tarball = tar_path.endswith('.tar.gz') or tar_path.endswith('.tgz')
        is_zip = tar_path.endswith('.zip')

        if data_type == "coci" and is_zip:
            # COCI è uno ZIP con ZIP interni
            self.process_nested_zip(tar_path)
        elif is_tarball:
            # META è un tar.gz normale
            self.process_tarball(tar_path, data_type)
        elif is_zip:
            # ZIP singolo
            self.process_single_zip(tar_path, data_type)
        else:
            print(f"❌ Formato file non supportato: {tar_path}")

    def process_tarball(self, tar_path, data_type):
        """Processa file .tar.gz con checkpoint"""
        
        # Se questa fase è già completata, salta
        if data_type == "meta" and self.meta_completed:
            print("✅ Fase META già completata, salto...")
            return
        
        if data_type == "coci" and self.coci_completed:
            print("✅ Fase COCI già completata, salto...")
            return
        
        total_csvs = 0
        total_rows = 0
        total_matches = 0
        first_match_csv = None
        
        try:
            print("Apertura archivio TAR.GZ...")
            with tarfile.open(tar_path, 'r:gz') as tar:
                members = tar.getmembers()
                csv_members = [m for m in members if m.name.endswith('.csv') and m.isfile()]
                
                print(f"✓ Trovati {len(csv_members)} file CSV nell'archivio")
                
                # Filtra CSV già processati
                csv_to_process = []
                for member in csv_members:
                    csv_name = os.path.basename(member.name)
                    if csv_name not in self.processed_csvs:
                        csv_to_process.append(member)
                
                if len(csv_to_process) < len(csv_members):
                    skipped = len(csv_members) - len(csv_to_process)
                    print(f"⏭️  Saltati {skipped} CSV già processati")
                
                print(f"📝 Da processare: {len(csv_to_process)} CSV")
                print()
                
                for i, member in enumerate(csv_to_process, 1):
                    total_csvs += 1
                    csv_name = os.path.basename(member.name)
                    
                    print(f"  [{i}/{len(csv_to_process)}] Processando: {csv_name}")
                    
                    csv_file = tar.extractfile(member)
                    if csv_file is None:
                        print(f"      ⚠️  Impossibile estrarre {csv_name}")
                        continue
                    
                    try:
                        rows, matches = self.process_csv_file(csv_file, csv_name, data_type)
                        total_rows += rows
                        total_matches += matches
                        
                        # Segna come processato
                        self.processed_csvs.add(csv_name)
                        
                        if matches > 0 and first_match_csv is None:
                            first_match_csv = i
                            print(f"      ✅ PRIMO MATCH TROVATO nel CSV #{i}!")
                        
                        if data_type == "meta":
                            print(f"      ✓ Righe: {rows:,}, Pubblicazioni trovate: {matches}")
                        else:
                            print(f"      ✓ Righe: {rows:,}, Citazioni trovate: {matches}")
                        
                        # Salva checkpoint ogni 10 CSV
                        if total_csvs % 10 == 0:
                            self.save_checkpoint(phase=data_type)
                    
                    except KeyboardInterrupt:
                        print("\n\n⚠️  INTERRUZIONE RILEVATA!")
                        print("💾 Salvataggio checkpoint prima di uscire...")
                        self.save_checkpoint(phase=data_type)
                        raise
                    
                    except Exception as e:
                        print(f"      ❌ ERRORE: {e}")
                        # Salva checkpoint anche in caso di errore
                        self.save_checkpoint(phase=data_type)
                    
                    finally:
                        csv_file.close()
            
            # Segna fase come completata
            if data_type == "meta":
                self.meta_completed = True
            else:
                self.coci_completed = True
            
            # Salva checkpoint finale
            self.save_checkpoint(phase=data_type)
        
        except KeyboardInterrupt:
            print("\n⚠️  Processo interrotto dall'utente")
            raise
        
        except Exception as e:
            print(f"❌ ERRORE aprendo TAR.GZ: {e}")
            import traceback
            traceback.print_exc()
            return
        
        self.print_statistics(data_type, total_csvs, total_rows, total_matches, first_match_csv)

    def process_nested_zip(self, zip_path):
        """Processa ZIP con ZIP interni (COCI) con checkpoint"""
        
        if self.coci_completed:
            print("✅ Fase COCI già completata, salto...")
            return
        
        from zipfile import ZipFile
        
        total_inner_zips = 0
        total_csvs = 0
        total_rows = 0
        total_matches = 0
        
        try:
            print("Apertura archivio ZIP principale...")
            with ZipFile(zip_path, 'r') as outer_zip:
                zip_list = [name for name in outer_zip.namelist() if name.endswith('.zip')]
                
                print(f"✓ Trovati {len(zip_list)} file ZIP interni")
                print()
                
                for i, inner_zip_name in enumerate(sorted(zip_list), 1):
                    total_inner_zips += 1
                    zip_basename = os.path.basename(inner_zip_name)
                    
                    # Salta se già processato
                    if zip_basename in self.processed_csvs:
                        print(f"  [{i}/{len(zip_list)}] ⏭️  Saltato (già processato): {zip_basename}")
                        continue
                    
                    print(f"  [{i}/{len(zip_list)}] Processando ZIP: {zip_basename}")
                    
                    # Estrai ZIP interno in memoria
                    inner_zip_data = outer_zip.read(inner_zip_name)
                    
                    try:
                        from io import BytesIO
                        with ZipFile(BytesIO(inner_zip_data), 'r') as inner_zip:
                            csv_list = [name for name in inner_zip.namelist() if name.endswith('.csv')]
                            
                            print(f"      Trovati {len(csv_list)} CSV")
                            
                            for csv_name in csv_list:
                                total_csvs += 1
                                csv_file = inner_zip.open(csv_name)
                                
                                try:
                                    rows, matches = self.process_csv_file(csv_file, csv_name, "coci")
                                    total_rows += rows
                                    total_matches += matches
                                    
                                    if total_csvs % 10 == 0:
                                        print(f"      CSV processati: {total_csvs}, Citazioni: {total_matches}", end='\r')
                                
                                except Exception as e:
                                    print(f"\n      ❌ ERRORE CSV {csv_name}: {e}")
                                
                                finally:
                                    csv_file.close()
                        
                        # Segna ZIP come processato
                        self.processed_csvs.add(zip_basename)
                        
                        # Salva checkpoint ogni 5 ZIP
                        if total_inner_zips % 5 == 0:
                            self.save_checkpoint(phase="coci")
                        
                        print(f"      ✓ ZIP completato")
                    
                    except KeyboardInterrupt:
                        print("\n\n⚠️  INTERRUZIONE RILEVATA!")
                        print("💾 Salvataggio checkpoint prima di uscire...")
                        self.save_checkpoint(phase="coci")
                        raise
                    
                    except Exception as e:
                        print(f"      ❌ ERRORE ZIP interno: {e}")
                        self.save_checkpoint(phase="coci")
            
            # Segna fase come completata
            self.coci_completed = True
            self.save_checkpoint(phase="coci")
        
        except KeyboardInterrupt:
            print("\n⚠️  Processo interrotto dall'utente")
            raise
        
        except Exception as e:
            print(f"❌ ERRORE aprendo ZIP: {e}")
            import traceback
            traceback.print_exc()
            return
        
        print()
        self.print_statistics("coci", total_csvs, total_rows, total_matches, None)

    def process_single_zip(self, zip_path, data_type):
        """Processa singolo ZIP"""
        from zipfile import ZipFile

        total_csvs = 0
        total_rows = 0
        total_matches = 0

        try:
            with ZipFile(zip_path, 'r') as zf:
                csv_list = [name for name in zf.namelist() if name.endswith('.csv')]

                for csv_name in csv_list:
                    total_csvs += 1
                    csv_file = zf.open(csv_name)

                    try:
                        rows, matches = self.process_csv_file(csv_file, csv_name, data_type)
                        total_rows += rows
                        total_matches += matches
                    finally:
                        csv_file.close()

        except Exception as e:
            print(f"❌ ERRORE: {e}")
            return

        self.print_statistics(data_type, total_csvs, total_rows, total_matches, None)

    def print_statistics(self, data_type, total_csvs, total_rows, total_matches, first_match_csv):
        """Stampa statistiche finali"""
        print(f"{'='*70}")
        print(f"STATISTICHE {data_type.upper()}")
        print(f"{'='*70}")
        print(f"CSV processati: {total_csvs}")
        print(f"Righe totali: {total_rows:,}")
        if data_type == "meta":
            print(f"Pubblicazioni trovate: {total_matches}")
            if first_match_csv:
                print(f"Primo match nel CSV: #{first_match_csv}")
            if total_matches > 0:
                total_authors = sum(len(p['authors']) for p in self.publications.values())
                print(f"Totale autori (tutti con ORCID): {total_authors}")
        else:
            print(f"Citazioni trovate: {total_matches}")
        print(f"{'='*70}")

    def merge_citations_into_publications(self):
        """
        Unisce i dati delle citazioni nelle pubblicazioni
        """
        print("\n" + "="*70)
        print("UNIONE CITAZIONI CON METADATI")
        print("="*70)

        pubs_with_citations = 0
        for pub_id, pub_data in self.publications.items():
            if pub_id in self.citations:
                pub_data["outgoing_citations"] = self.citations[pub_id]["outgoing"]
                pub_data["incoming_citations"] = self.citations[pub_id]["incoming"]
                pubs_with_citations += 1

        print(f"Pubblicazioni con citazioni: {pubs_with_citations}/{len(self.publications)}")
        print("="*70)

    def generate_output_json(self, output_path):
        """
        Genera il file JSON finale con ID corti per le pubblicazioni
        e OMID originali per le citazioni
        """
        print("\n" + "="*70)
        print("GENERAZIONE FILE JSON")
        print("="*70)

        # Genera short IDs per tutte le pubblicazioni
        print("Generazione ID corti per pubblicazioni...")
        for pub_omid in self.publications.keys():
            if pub_omid not in self.omid_to_short_id:
                self.omid_to_short_id[pub_omid] = self.generate_short_id()

        # Costruisci output con short IDs per le pubblicazioni
        # ma mantieni OMID originali per le citazioni
        output_data = {}
        for pub_omid, pub_data in self.publications.items():
            short_id = self.omid_to_short_id[pub_omid]

            # Crea la pubblicazione con short ID
            output_data[short_id] = {
                "id": short_id,  # ID ripetuto come campo interno
                "title": pub_data.get("title", ""),
                "abstract": pub_data.get("abstract", ""),
                "keywords": pub_data.get("keywords", []),
                "authors": pub_data.get("authors", []),
                "venue": pub_data.get("venue", ""),
                "year": pub_data.get("year"),
                "outgoing_citations": pub_data.get("outgoing_citations", []),  # OMID originali
                "incoming_citations": pub_data.get("incoming_citations", [])   # OMID originali
            }

        print(f"Scrittura file: {output_path}")
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        # Statistiche finali
        file_size = os.path.getsize(output_path) / (1024 * 1024)  # MB

        print(f"✓ File salvato con successo!")
        print(f"  Path: {os.path.abspath(output_path)}")
        print(f"  Dimensione: {file_size:.2f} MB")
        print(f"  Pubblicazioni: {len(output_data)}")
        print("="*70)

    def run(self, output_path="semanticweb_com_pub.json"):
        """
        Esegue l'intero processo di estrazione con checkpoint
        """
        print("\n" + "="*70)
        print("ESTRAZIONE DATI DA OPENCITATIONS")
        print("="*70)
        print(f"ISSN Target: {', '.join(self.target_issn)}")
        print(f"Filtro: Pubblicazioni con almeno 1 autore ORCID")
        print(f"Output: Mantiene SOLO gli autori con ORCID")
        print("="*70)
        
        try:
            # Step 1: Processa Meta
            if not self.meta_completed:
                print("\n📚 FASE 1: ESTRAZIONE METADATI")
                self.process_tar_gz(self.meta_path, "meta")
                
                if not self.publications:
                    print("\n" + "="*70)
                    print("⚠️  ATTENZIONE: NESSUNA PUBBLICAZIONE TROVATA!")
                    print("="*70)
                    print("\nPossibili cause:")
                    print(f"  • ISSN target non presenti nel dataset")
                    print("  • Nessun autore con ORCID per questi ISSN")
                    print("  • File Meta corrotto o formato diverso")
                    return
            else:
                print("\n✅ FASE 1: METADATI GIÀ COMPLETATI")
                print(f"   Pubblicazioni caricate: {len(self.publications)}")
            
            # Step 2: Processa COCI
            if not self.coci_completed:
                print("\n🔗 FASE 2: ESTRAZIONE CITAZIONI")
                self.process_tar_gz(self.coci_path, "coci")
            else:
                print("\n✅ FASE 2: CITAZIONI GIÀ COMPLETATE")
            
            # Step 3: Unisci dati
            print("\n🔄 FASE 3: UNIONE DATI")
            self.merge_citations_into_publications()
            
            # Step 4: Genera JSON
            print("\n💾 FASE 4: SALVATAGGIO")
            self.generate_output_json(output_path)
            
            # Cancella checkpoint dopo successo
            print("\n🗑️  Cancellazione checkpoint (completato con successo)...")
            self.clear_checkpoint()
            
            print("\n" + "="*70)
            print("✅ ESTRAZIONE COMPLETATA CON SUCCESSO!")
            print("="*70)
            print(f"\nFile output: {os.path.abspath(output_path)}")
            print()
        
        except KeyboardInterrupt:
            print("\n\n" + "="*70)
            print("⚠️  PROCESSO INTERROTTO")
            print("="*70)
            print(f"💾 Checkpoint salvato in: {self.checkpoint_dir}")
            print(f"📊 Stato attuale:")
            print(f"   - Pubblicazioni: {len(self.publications)}")
            print(f"   - CSV processati: {len(self.processed_csvs)}")
            print(f"   - META completata: {'✅' if self.meta_completed else '❌'}")
            print(f"   - COCI completata: {'✅' if self.coci_completed else '❌'}")
            print("\n💡 Rilancia lo script per riprendere da qui!")
            print("="*70)


def main():
    """
    Funzione principale
    """
    print("\n" + "="*70)
    print("OPENCITATIONS EXTRACTOR - CON CHECKPOINT")
    print("="*70)

    # ==========================================
    # ⚙️  CONFIGURA QUI I PERCORSI E ISSN
    # ==========================================

    META_PATH = r"D:\oc_meta_data_2025-06-06.tar.gz"
    COCI_PATH = r"D:\24356626.zip"

    OUTPUT_FILE = "multi_journal_output.json"

    # Lista di ISSN da estrarre
    TARGET_ISSN = [
    "1570-8268",  # Journal of Web Semantics (Elsevier)
    "1570-0844",  # Semantic Web Journal (IOS Press)
    "1936-6442",  # Journal of Semantic Web and Information Systems (IGI Global)
    "2332-7790",  # Transactions on Graph Data and Knowledge (ACM)
    "1861-2032",  # Journal on Data Semantics (Springer)
    "0169-023X",  # Data & Knowledge Engineering (Elsevier)
    "1570-5838",  # Applied Ontology (IOS Press)
    "2451-8484"   # Data Science: Methods, Infrastructure, Applications (IOS Press)
]

    # ==========================================

    print(f"\n📋 CONFIGURAZIONE:")
    print(f"  Meta: {META_PATH}")
    print(f"  COCI: {COCI_PATH}")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"  Riviste target:")
    for issn in TARGET_ISSN:
        print(f"    - ISSN {issn}")

    # Verifica che i file esistano
    errors = []
    if not os.path.exists(META_PATH):
        errors.append(f"❌ File Meta non trovato: {META_PATH}")
    else:
        print(f"  ✓ Meta file OK")

    if not os.path.exists(COCI_PATH):
        errors.append(f"❌ File COCI non trovato: {COCI_PATH}")
    else:
        print(f"  ✓ COCI file OK")

    if errors:
        print("\n" + "="*70)
        print("ERRORI DI CONFIGURAZIONE")
        print("="*70)
        for error in errors:
            print(error)
        print("\n💡 Suggerimenti:")
        print("  • Verifica che i path siano corretti")
        print("  • Su Windows usa: r\"D:\\fra\\file.tar.gz\" (con r davanti)")
        print("  • Verifica che i file siano stati scaricati")
        print("  • Scarica COCI da: https://doi.org/10.6084/m9.figshare.6741422")
        return

    print("\n✓ Tutti i file sono presenti!")

    # Controlla se esiste checkpoint
    checkpoint_exists = Path("checkpoints/checkpoint_meta.json").exists()
    if checkpoint_exists:
        print("\n📂 CHECKPOINT TROVATO!")
        print("Vuoi:")
        print("  1. Riprendere da dove interrotto")
        print("  2. Ricominciare da zero (cancella checkpoint)")
        choice = input("\nScelta (1/2): ").strip()
        
        if choice == "2":
            import shutil
            shutil.rmtree("checkpoints", ignore_errors=True)
            print("🗑️  Checkpoint cancellato, ricomincio da zero...")
    else:
        # Conferma prima di iniziare
        print("\n⚠️  ATTENZIONE: Questo processo può richiedere diverse ore.")
        response = input("\nVuoi continuare? (s/n): ").strip().lower()
        
        if response not in ['s', 'si', 'sì', 'y', 'yes']:
            print("\n❌ Operazione annullata dall'utente.")
            return

    # Esegui estrazione
    extractor = OpenCitationsExtractor(META_PATH, COCI_PATH, TARGET_ISSN)
    extractor.run(OUTPUT_FILE)


if __name__ == "__main__":
    main()