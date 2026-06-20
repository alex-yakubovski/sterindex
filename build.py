#!/usr/bin/env python3
"""
SterIndex — build.py
Run this script to refresh the site.

What it does:
  1. Loads archive.json (all previously saved articles, newest first)
  2. Fetches RSS feeds (PubMed, FDA, CDC) — free, no key needed
  3. Merges new articles into the archive (deduplicates by URL)
  4. Calls Ollama (local, free) to write a descriptive summary for each NEW item
  5. Saves each new article as  articles/<slug>.html
  6. Rewrites paginated index pages:
       index.html   → articles  1–30   (latest)
       index2.html  → articles 31–60   etc.
  7. Writes legal.html

Requirements (one-time setup):
  1. Install Ollama:  https://ollama.com/download
  2. Pull a model:    ollama pull mistral
  3. pip install -r requirements.txt

Usage:
  python build.py
"""

import os, re, json, time, textwrap, random
from datetime import datetime, timezone
from pathlib import Path
from math import ceil

import httpx
import xml.etree.ElementTree as ET
from jinja2 import Environment, FileSystemLoader

# ── Config ─────────────────────────────────────────────────────────────────────

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"   # change to "llama3" or "phi3" if preferred

PAGE_SIZE     = 30
OUTPUT_DIR    = Path(__file__).parent
ARTICLES_DIR  = OUTPUT_DIR / "articles"
TEMPLATES_DIR = OUTPUT_DIR / "templates"
ARCHIVE_FILE    = OUTPUT_DIR / "archive.json"
COMPANIES_DIR   = OUTPUT_DIR / "companies"

ARTICLES_DIR.mkdir(exist_ok=True)
COMPANIES_DIR.mkdir(exist_ok=True)

# ── RSS Sources ────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    # ── PubMed: Sterile Processing (NCBI E-utilities — no bot blocking) ────────
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=surgical+instrument+sterilization&retmax=10&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=endoscope+reprocessing+high+level+disinfection&retmax=10&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=autoclave+steam+sterilization+hospital&retmax=10&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=sterile+processing+department+SPD&retmax=8&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=medical+device+decontamination+reprocessing&retmax=8&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=surgical+site+infection+instrument+sterilization&retmax=8&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=hydrogen+peroxide+plasma+sterilization+medical&retmax=8&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=flexible+endoscope+disinfection+contamination&retmax=8&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=central+sterile+supply+department&retmax=8&retmode=json"),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=biological+indicator+sterilization+validation&retmax=8&retmode=json"),

    # ── FDA: Medical Device Safety Alerts only ─────────────────────────────────
    # Uses the medical devices specific feed, not the general MedWatch feed
    ("FDA Medical Devices", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medical-devices/rss.xml"),
    ("FDA Recalls (Devices)", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/recalls/rss.xml"),
]

# ── Verified companies (top 20 industry leaders) ──────────────────────────────

VERIFIED_COMPANIES = {
    "STERIS plc",
    "Getinge AB",
    "Advanced Sterilization Products (ASP)",
    "Karl Storz SE & Co. KG",
    "Olympus Corporation",
    "Medtronic plc",
    "Stryker Corporation",
    "3M Health Care",
    "Aesculap AG (B. Braun)",
    "Ecolab Inc.",
    "Smith & Nephew plc",
    "Zimmer Biomet Holdings",
    "Arthrex Inc.",
    "Medline Industries",
    "DuPont (Tyvek Medical)",
    "Belimed AG",
    "Tuttnauer USA",
    "Integra LifeSciences",
    "ConMed Corporation",
    "Nanosonics Ltd.",
}

# ── Full company directory (100 entries) ───────────────────────────────────────

COMPANIES = [
  {"company_name":"STERIS plc","hq_country":"USA / Ireland","main_category":"Capital Equipment","key_products":"AMSCO steam sterilizers, V-PRO low-temp sterilizers, washer-disinfectors, sterility assurance","fda_approved":True,"website_url":"https://www.steris.com"},
  {"company_name":"Getinge AB","hq_country":"Sweden","main_category":"Capital Equipment","key_products":"GSS steam sterilizers, PHS washer-disinfectors, endoscope reprocessors, sterility monitoring","fda_approved":True,"website_url":"https://www.getinge.com"},
  {"company_name":"Belimed AG","hq_country":"Switzerland","main_category":"Capital Equipment","key_products":"WD-series washer-disinfectors, steam sterilizers, sterile processing workflow systems","fda_approved":True,"website_url":"https://www.belimed.com"},
  {"company_name":"Tuttnauer USA","hq_country":"Israel / USA","main_category":"Capital Equipment","key_products":"Bench-top and floor-standing autoclaves, washer-disinfectors, sterilization accessories","fda_approved":True,"website_url":"https://tuttnauer.com"},
  {"company_name":"Systec GmbH","hq_country":"Germany","main_category":"Capital Equipment","key_products":"Laboratory and medical autoclaves, bench-top steam sterilizers, vacuum sterilizers","fda_approved":False,"website_url":"https://www.systec-lab.com"},
  {"company_name":"Matachana Group","hq_country":"Spain","main_category":"Capital Equipment","key_products":"Steam sterilizers, EO sterilizers, washer-disinfectors, low-temp H2O2 sterilizers","fda_approved":True,"website_url":"https://www.matachana.com"},
  {"company_name":"Fedegari Group","hq_country":"Italy","main_category":"Capital Equipment","key_products":"Pharmaceutical and medical steam sterilizers, overkill sterilization systems, isolators","fda_approved":True,"website_url":"https://www.fedegari.com"},
  {"company_name":"Advanced Sterilization Products (ASP)","hq_country":"USA","main_category":"Capital Equipment","key_products":"STERRAD hydrogen peroxide plasma sterilizers, CYCLESURE biological indicators","fda_approved":True,"website_url":"https://www.asp.com"},
  {"company_name":"Andersen Products","hq_country":"USA","main_category":"Capital Equipment","key_products":"EtO sterilizers, ethylene oxide sterilization accessories, aeration units","fda_approved":True,"website_url":"https://www.anpro.com"},
  {"company_name":"TSO3 Inc.","hq_country":"Canada","main_category":"Capital Equipment","key_products":"Sterizone VP4 ozone sterilizer, low-temperature sterilization for heat-sensitive devices","fda_approved":True,"website_url":"https://www.tso3.com"},
  {"company_name":"Medivators (Cantel Medical)","hq_country":"USA","main_category":"Capital Equipment","key_products":"DSD-201 endoscope reprocessors, Advantage Plus AER, high-level disinfection systems","fda_approved":True,"website_url":"https://www.medivators.com"},
  {"company_name":"Wassenburg Medical","hq_country":"Netherlands","main_category":"Capital Equipment","key_products":"WD440 endoscope washer-disinfectors, drying and storage cabinets, leak testers","fda_approved":False,"website_url":"https://www.wassenburg.com"},
  {"company_name":"Aesculap AG (B. Braun)","hq_country":"Germany","main_category":"Surgical Instruments","key_products":"Forceps, needle holders, scissors, retractors, sterile container trays, HF surgery","fda_approved":True,"website_url":"https://www.aesculap.com"},
  {"company_name":"Karl Storz SE & Co. KG","hq_country":"Germany","main_category":"Surgical Instruments","key_products":"Rigid endoscopes, laparoscopes, arthroscopes, MIS instruments, sterile trays","fda_approved":True,"website_url":"https://www.karlstorz.com"},
  {"company_name":"Stryker Corporation","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Orthopedic instruments, power tools, trauma sets, sterile container systems","fda_approved":True,"website_url":"https://www.stryker.com"},
  {"company_name":"Olympus Corporation","hq_country":"Japan","main_category":"Surgical Instruments","key_products":"Flexible and rigid endoscopes, biopsy forceps, laparoscopic instruments","fda_approved":True,"website_url":"https://www.olympus-global.com"},
  {"company_name":"Integra LifeSciences","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Neurosurgical instruments, microsurgery tools, Jarit general surgery instruments","fda_approved":True,"website_url":"https://www.integralife.com"},
  {"company_name":"Medline Industries","hq_country":"USA","main_category":"Surgical Instruments","key_products":"General surgery instruments, OR trays, custom procedure trays, clamps, retractors","fda_approved":True,"website_url":"https://www.medline.com"},
  {"company_name":"Symmetry Surgical","hq_country":"USA / Pakistan","main_category":"Surgical Instruments","key_products":"Electrosurgical instruments, retractors, clamps, general surgery hand instruments","fda_approved":True,"website_url":"https://www.symmetrysurgical.com"},
  {"company_name":"Sklar Surgical Instruments","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Full-line surgical instruments, forceps, needle holders, scissors, clamps, curettes","fda_approved":True,"website_url":"https://www.sklarcorp.com"},
  {"company_name":"Roboz Surgical Instrument Co.","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Microsurgery instruments, ophthalmic instruments, neurosurgery tools","fda_approved":True,"website_url":"https://www.roboz.com"},
  {"company_name":"Hu-Friedy (SDI Health)","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Dental and surgical instruments, cassette trays, instrument management systems","fda_approved":True,"website_url":"https://www.hu-friedy.com"},
  {"company_name":"Ruhof Corporation","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Endozime enzymatic detergents, instrument brushes, AW multi-enzymatic cleaners","fda_approved":True,"website_url":"https://www.ruhof.com"},
  {"company_name":"Ecolab Inc.","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Instrument detergents, disinfectants, enzymatic pre-cleaners, surface wipes","fda_approved":True,"website_url":"https://www.ecolab.com"},
  {"company_name":"Certol International","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"ProEZ enzymatic detergents, instrument lubricants, pre-cleaning foams and sprays","fda_approved":True,"website_url":"https://www.certol.com"},
  {"company_name":"3M Health Care","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Attest biological indicators, Comply chemical indicators, sterilization pouches, tape","fda_approved":True,"website_url":"https://www.3m.com/healthcare"},
  {"company_name":"Crosstex International","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Sterilization pouches, self-seal bags, biological indicators, chemical integrators","fda_approved":True,"website_url":"https://www.crosstex.com"},
  {"company_name":"Propper Manufacturing","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Biological indicators, chemical indicators, sterilization monitoring, steam test packs","fda_approved":True,"website_url":"https://www.propper.com"},
  {"company_name":"Steris (Verify brand)","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Biological and chemical indicators, Reliance enzymatic detergents, instrument lubricants","fda_approved":True,"website_url":"https://www.steris.com/consumables"},
  {"company_name":"Aesculap (Case & Tray Systems)","hq_country":"Germany","main_category":"Sterile Packaging & Containers","key_products":"Aesculap rigid sterilization containers, Inlet system, filter retention plates","fda_approved":True,"website_url":"https://www.aesculap.com/sterile-containers"},
  {"company_name":"Medline Sterile Packaging","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Sterilization wraps, CSR wrap, poly pouches, peel pouches, header bags","fda_approved":True,"website_url":"https://www.medline.com/sterile-packaging"},
  {"company_name":"Halyard Health (Owens & Minor)","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"KIMGUARD sterilization wrap, ULTRA WRAP, polypropylene non-woven wraps","fda_approved":True,"website_url":"https://www.halyardhealth.com"},
  {"company_name":"Symmetry Medical (Integer Holdings)","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Custom sterile packaging, rigid trays, thermoformed packaging for medical devices","fda_approved":True,"website_url":"https://www.integerholdings.com"},
  {"company_name":"Fortive / Censis Technologies","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Instrument tracking software, SPD tray management, sterility assurance documentation","fda_approved":False,"website_url":"https://www.censistechnologies.com"},
  {"company_name":"SciCan Ltd.","hq_country":"Canada","main_category":"Capital Equipment","key_products":"STATIM cassette autoclaves, HYDRIM washer-disinfectors, BRAVO bench-top sterilizers","fda_approved":True,"website_url":"https://www.scican.com"},
  {"company_name":"Melag Medizintechnik","hq_country":"Germany","main_category":"Capital Equipment","key_products":"Vacuklav bench-top autoclaves, Cliniclave floor-standing sterilizers, sealing devices","fda_approved":False,"website_url":"https://www.melag.com"},
  {"company_name":"Sakura Seiki Co.","hq_country":"Japan","main_category":"Capital Equipment","key_products":"EO sterilizers, low-temperature plasma sterilization equipment, accessory sets","fda_approved":False,"website_url":"https://www.sakura-seiki.co.jp"},
  {"company_name":"Priorclave Ltd.","hq_country":"United Kingdom","main_category":"Capital Equipment","key_products":"Research and laboratory autoclaves, top-loading and front-loading steam sterilizers","fda_approved":False,"website_url":"https://www.priorclave.co.uk"},
  {"company_name":"Celitron Medical Technologies","hq_country":"Hungary","main_category":"Capital Equipment","key_products":"ISS medical waste autoclaves, compact steam sterilizers for clinical settings","fda_approved":False,"website_url":"https://www.celitron.com"},
  {"company_name":"BHT Hygienetechnik","hq_country":"Germany","main_category":"Capital Equipment","key_products":"Large-capacity washer-disinfectors, tunnel washers, CSSD workflow automation systems","fda_approved":False,"website_url":"https://www.bht-online.de"},
  {"company_name":"Shinva Medical Instrument Co.","hq_country":"China","main_category":"Capital Equipment","key_products":"Steam sterilizers, EO sterilizers, washer-disinfectors, plasma sterilizers","fda_approved":False,"website_url":"https://www.shinva.com"},
  {"company_name":"W&H Dentalwerk","hq_country":"Austria","main_category":"Capital Equipment","key_products":"Lisa and Lexa autoclaves, Teon washer-disinfectors for dental and surgical instruments","fda_approved":True,"website_url":"https://www.wh.com"},
  {"company_name":"Noxilizer Inc.","hq_country":"USA","main_category":"Capital Equipment","key_products":"Nitrogen dioxide (NO2) sterilization systems, terminal sterilization for medical devices","fda_approved":True,"website_url":"https://www.noxilizer.com"},
  {"company_name":"Sterigenics International","hq_country":"USA","main_category":"Capital Equipment","key_products":"Contract EO sterilization, gamma irradiation, e-beam sterilization services","fda_approved":True,"website_url":"https://www.sterigenics.com"},
  {"company_name":"Richard Wolf GmbH","hq_country":"Germany","main_category":"Surgical Instruments","key_products":"Rigid endoscopes, laparoscopic instruments, urological instruments, arthroscopes","fda_approved":True,"website_url":"https://www.richard-wolf.com"},
  {"company_name":"Teleflex Inc.","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Laparoscopic instruments, surgical access devices, retractors, vessel loops","fda_approved":True,"website_url":"https://www.teleflex.com"},
  {"company_name":"Medtronic plc","hq_country":"Ireland / USA","main_category":"Surgical Instruments","key_products":"Electrosurgical forceps, laparoscopic stapling instruments, neurosurgical tools","fda_approved":True,"website_url":"https://www.medtronic.com"},
  {"company_name":"Smith & Nephew plc","hq_country":"United Kingdom","main_category":"Surgical Instruments","key_products":"Arthroscopic instruments, orthopedic power tools, trauma instrument sets","fda_approved":True,"website_url":"https://www.smith-nephew.com"},
  {"company_name":"Zimmer Biomet Holdings","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Orthopedic surgery instrument sets, reamer systems, loaner instrument trays","fda_approved":True,"website_url":"https://www.zimmerbiomet.com"},
  {"company_name":"ConMed Corporation","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Electrosurgical instruments, laparoscopic instruments, arthroscopic shaver systems","fda_approved":True,"website_url":"https://www.conmed.com"},
  {"company_name":"Jarit (Integra LifeSciences)","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Premium forceps, clamps, needle holders, retractors, Mayo and Metzenbaum scissors","fda_approved":True,"website_url":"https://www.integralife.com/jarit"},
  {"company_name":"Lawton GmbH & Co. KG","hq_country":"Germany","main_category":"Surgical Instruments","key_products":"Microsurgical instruments, neurosurgical instruments, ophthalmic surgery tools","fda_approved":True,"website_url":"https://www.lawton-instruments.com"},
  {"company_name":"Novo Surgical Inc.","hq_country":"USA","main_category":"Surgical Instruments","key_products":"General surgery instruments, German-grade forceps, clamps, scissors, retractors","fda_approved":True,"website_url":"https://www.novosurgical.com"},
  {"company_name":"Tetra Medical Supply Corp.","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Custom surgical sets, instrument trays, specialty procedure packs, OR kits","fda_approved":True,"website_url":"https://www.tetramedical.com"},
  {"company_name":"Biotrol International","hq_country":"France","main_category":"Instrument Care & Consumables","key_products":"Enzymatic instrument cleaners, surface disinfectants, pre-soaking tablets","fda_approved":False,"website_url":"https://www.biotrol.com"},
  {"company_name":"Getinge (Lancer brand)","hq_country":"Sweden","main_category":"Instrument Care & Consumables","key_products":"Lancer enzymatic detergents, washer chemistry, biological and chemical indicators","fda_approved":True,"website_url":"https://www.getinge.com/consumables"},
  {"company_name":"Healthmark Industries","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Instrument brushes, test packs, protective tip covers, instrument inspection accessories","fda_approved":True,"website_url":"https://www.hmark.com"},
  {"company_name":"Gke GmbH","hq_country":"Germany","main_category":"Instrument Care & Consumables","key_products":"Chemical indicators, biological indicators, process challenge devices for steam and EO","fda_approved":True,"website_url":"https://www.gke-online.de"},
  {"company_name":"Terragene SA","hq_country":"Argentina","main_category":"Instrument Care & Consumables","key_products":"Biological indicators (Bionova), chemical integrators, rapid readout BI systems","fda_approved":True,"website_url":"https://www.terragene.com.ar"},
  {"company_name":"Mesa Labs (Raven Biological)","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Biological indicators, chemical indicators, rapid readout systems for steam and EO","fda_approved":True,"website_url":"https://www.mesalabs.com"},
  {"company_name":"Stericlin (Cantel / Steris)","hq_country":"Germany","main_category":"Sterile Packaging & Containers","key_products":"Sterilization pouches, reels, non-woven wraps, SMS wrapping material","fda_approved":True,"website_url":"https://www.stericlin.de"},
  {"company_name":"Fortex Medical","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Sterilization reels, heat-seal pouches, self-seal bags, instrument protection products","fda_approved":True,"website_url":"https://www.fortexmedical.com"},
  {"company_name":"Surgipak (Ahlstrom)","hq_country":"USA / Finland","main_category":"Sterile Packaging & Containers","key_products":"Medical-grade non-woven sterilization wrap, SMS wraps, crepe wrap","fda_approved":True,"website_url":"https://www.ahlstrom.com/medical"},
  {"company_name":"KenGuard (IST)","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Rigid sterilization containers, filter retention systems, instrument protection trays","fda_approved":True,"website_url":"https://www.kenguard.com"},
  {"company_name":"Sterilis Solutions","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"On-site medical waste sterilization systems, point-of-care sharps processing","fda_approved":True,"website_url":"https://www.sterilissolutions.com"},
  {"company_name":"Euronda S.p.A.","hq_country":"Italy","main_category":"Capital Equipment","key_products":"E9 Next autoclaves, bench-top steam sterilizers, sealing machines, traceability systems","fda_approved":False,"website_url":"https://www.euronda.com"},
  {"company_name":"Biobase Group","hq_country":"China","main_category":"Capital Equipment","key_products":"Vertical and horizontal autoclaves, bench-top steam sterilizers, laboratory washers","fda_approved":False,"website_url":"https://www.biobase-china.com"},
  {"company_name":"Astell Scientific","hq_country":"United Kingdom","main_category":"Capital Equipment","key_products":"Front and top-loading autoclaves, laboratory steam sterilizers, custom CSSD systems","fda_approved":False,"website_url":"https://www.astell.com"},
  {"company_name":"Eschmann Equipment","hq_country":"United Kingdom","main_category":"Capital Equipment","key_products":"Little Sister bench-top autoclaves, washer-disinfectors for dental and surgical use","fda_approved":False,"website_url":"https://www.eschmann.co.uk"},
  {"company_name":"Hanshin Medical Co.","hq_country":"South Korea","main_category":"Capital Equipment","key_products":"Plasma sterilizers, low-temperature H2O2 sterilization systems, steam autoclaves","fda_approved":True,"website_url":"https://www.hanshinmedical.com"},
  {"company_name":"Midmark Corporation","hq_country":"USA","main_category":"Capital Equipment","key_products":"M9 and M11 ultraclave autoclaves, statim cassette sterilizers for office-based practice","fda_approved":True,"website_url":"https://www.midmark.com"},
  {"company_name":"Smeg Instruments","hq_country":"Italy","main_category":"Capital Equipment","key_products":"Washer-disinfectors for surgical instruments, endoscope washers, thermal disinfectors","fda_approved":False,"website_url":"https://www.smeginstruments.com"},
  {"company_name":"Arthrex Inc.","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Arthroscopic instruments, orthopedic implant sets, minimally invasive surgery tools","fda_approved":True,"website_url":"https://www.arthrex.com"},
  {"company_name":"Mizuho OSI","hq_country":"USA / Japan","main_category":"Surgical Instruments","key_products":"Surgical positioning systems, retractor systems, spinal surgery instrument sets","fda_approved":True,"website_url":"https://www.mizuhosi.com"},
  {"company_name":"Pendiq GmbH","hq_country":"Germany","main_category":"Surgical Instruments","key_products":"Precision microsurgical instruments, ophthalmic forceps, iris scissors, cannulas","fda_approved":False,"website_url":"https://www.pendiq.com"},
  {"company_name":"Globus Medical Inc.","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Spine surgery instrument systems, retractors, neuro instrument sets, robotic tools","fda_approved":True,"website_url":"https://www.globusmedical.com"},
  {"company_name":"Surgical Holdings","hq_country":"United Kingdom","main_category":"Surgical Instruments","key_products":"General and specialist surgical instruments, bespoke instrument sets, repair services","fda_approved":False,"website_url":"https://www.surgicalholdings.co.uk"},
  {"company_name":"Mopec Inc.","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Pathology and autopsy instruments, dissection sets, tissue processing tools","fda_approved":True,"website_url":"https://www.mopec.com"},
  {"company_name":"Wexler Surgical","hq_country":"USA","main_category":"Surgical Instruments","key_products":"Cardiovascular surgery instruments, thoracic retractors, vascular clamps, valve instruments","fda_approved":True,"website_url":"https://www.wexlersurgical.com"},
  {"company_name":"Micro-Scientific Industries","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Klenzyme enzymatic detergents, instrument lubricants, surface disinfectant wipes","fda_approved":True,"website_url":"https://www.micro-scientific.com"},
  {"company_name":"Steris (Prolystica brand)","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Prolystica ultra-concentrate detergents, enzymatic presoak, neutral detergents for WD","fda_approved":True,"website_url":"https://www.steris.com/prolystica"},
  {"company_name":"Hu-Friedy (IMS Cleaning Line)","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"IMS instrument management cassettes, enzymatic cleaners, instrument inspection loops","fda_approved":True,"website_url":"https://www.hu-friedy.com/ims"},
  {"company_name":"Cantel Medical (Endoscopy Care)","hq_country":"USA","main_category":"Instrument Care & Consumables","key_products":"Endoscope cleaning brushes, channel flushing aids, enzymatic detergents for flexible scopes","fda_approved":True,"website_url":"https://www.medivators.com/endoscopy-consumables"},
  {"company_name":"Nanosonics Ltd.","hq_country":"Australia","main_category":"Instrument Care & Consumables","key_products":"Trophon EPR ultrasound probe disinfection system, trophon2, probe disinfection consumables","fda_approved":True,"website_url":"https://www.nanosonics.com.au"},
  {"company_name":"Dentsply Sirona (Infection Control)","hq_country":"USA / Germany","main_category":"Instrument Care & Consumables","key_products":"Surface disinfectants, instrument presoak solutions, barrier protection consumables","fda_approved":True,"website_url":"https://www.dentsplysirona.com/infection-control"},
  {"company_name":"Alkapharm (Pharmax Ltd.)","hq_country":"United Kingdom","main_category":"Instrument Care & Consumables","key_products":"Enzymatic instrument detergents, endoscope cleaning agents, disinfection chemistry","fda_approved":False,"website_url":"https://www.alkapharm.co.uk"},
  {"company_name":"Wipak Group","hq_country":"Finland","main_category":"Sterile Packaging & Containers","key_products":"Medical-grade sterilization pouches, Tyvek lidding films, flexible barrier packaging","fda_approved":True,"website_url":"https://www.wipak.com/medical"},
  {"company_name":"DuPont (Tyvek Medical)","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Tyvek 1073B and 1059B medical packaging material, surgical gown fabric, sterile barrier systems","fda_approved":True,"website_url":"https://www.dupont.com/tyvek-medical"},
  {"company_name":"Oliver Healthcare Packaging","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Custom sterile packaging, Tyvek pouches, lidding films, medical thermoforms","fda_approved":True,"website_url":"https://www.oliverhealthcare.com"},
  {"company_name":"Placon Corporation","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Thermoformed sterile trays, custom medical device packaging, blister packaging","fda_approved":True,"website_url":"https://www.placon.com/medical"},
  {"company_name":"Amcor plc","hq_country":"Australia / Switzerland","main_category":"Sterile Packaging & Containers","key_products":"Flexible sterile barrier packaging, peelable lidding, medical pouches and thermoforms","fda_approved":True,"website_url":"https://www.amcor.com/markets/healthcare"},
  {"company_name":"Dai Nippon Printing (DNP Medical)","hq_country":"Japan","main_category":"Sterile Packaging & Containers","key_products":"Medical packaging films, sterile barrier pouches, Tyvek-compatible laminate packaging","fda_approved":False,"website_url":"https://www.dnp.co.jp/eng/business/medical"},
  {"company_name":"Volk Optical (Container Inserts)","hq_country":"USA","main_category":"Sterile Packaging & Containers","key_products":"Ophthalmic instrument containers, silicone protection mats, custom foam inserts for trays","fda_approved":True,"website_url":"https://www.volk.com"},
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80]


def unique_slug(title: str, existing: set) -> str:
    base = slugify(title)
    slug, n = base, 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    existing.add(slug)
    return slug


def clean_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]


def fmt_date(raw: str) -> str:
    """
    Parse any date string and return clean Month DD, YYYY format.
    Falls back to Month YYYY if only month+year is available.
    """
    if not raw:
        return ""
    raw = raw.strip()

    # Standard datetime formats
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d %b %Y %H:%M:%S %z",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(raw, fmt).strftime("%B %d, %Y")
        except Exception:
            pass

    # PubMed: "2026 May 08" or "2026 May 8"
    m = re.match(r'(\d{4})\s+([A-Za-z]+)\s+(\d{1,2})', raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {int(m.group(3)):02d}", "%Y %b %d").strftime("%B %d, %Y")
        except Exception:
            pass

    # PubMed: "2026 May" — month + year only
    m = re.match(r'(\d{4})\s+([A-Za-z]+)', raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y %b").strftime("%B %Y")
        except Exception:
            pass

    # "May 2026" or "May, 2026"
    m = re.match(r'([A-Za-z]+)[,\s]+(\d{4})', raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%B %Y").strftime("%B %Y")
        except Exception:
            pass

    # Year only: "2026"
    m = re.match(r'^(\d{4})$', raw)
    if m:
        return m.group(1)

    return raw[:20]

def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80]


def unique_slug(title: str, existing: set) -> str:
    base = slugify(title)
    slug, n = base, 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    existing.add(slug)
    return slug


def clean_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]


def fmt_date(raw: str) -> str:
    """
    Parse any date string we encounter and return clean Month DD, YYYY format.
    Falls back to Month YYYY if only month+year is determinable.
    Falls back to raw string truncated if nothing works.
    """
    if not raw:
        return ""
    raw = raw.strip()

    # Full datetime formats
    full_fmts = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d %b %Y %H:%M:%S %z",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]
    for fmt in full_fmts:
        try:
            return datetime.strptime(raw, fmt).strftime("%B %d, %Y")
        except Exception:
            pass

    # PubMed returns formats like "2026 May 08" or "2026 May" or "2026"
    import re
    # "2026 May 08" or "2026 May 8"
    m = re.match(r"(\d{4})\s+([A-Za-z]+)\s+(\d{1,2})", raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%Y %b %d").strftime("%B %d, %Y")
        except Exception:
            pass

    # "2026 May" — month + year only
    m = re.match(r"(\d{4})\s+([A-Za-z]+)", raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y %b").strftime("%B %Y")
        except Exception:
            pass

    # "May 2026" or "May, 2026"
    m = re.match(r"([A-Za-z]+)[,\s]+(\d{4})", raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%B %Y").strftime("%B %Y")
        except Exception:
            pass

    # Year only — "2026"
    m = re.match(r"^(\d{4})$", raw)
    if m:
        return m.group(1)

    # Last resort — return first 20 chars
    return raw[:20]


def page_filename(n: int) -> str:
    return "index.html" if n == 1 else f"index{n}.html"


# ── Archive ────────────────────────────────────────────────────────────────────

def load_archive() -> list[dict]:
    if ARCHIVE_FILE.exists():
        try:
            return json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] Could not read archive: {e}")
    return []


def save_archive(articles: list[dict]) -> None:
    ARCHIVE_FILE.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── RSS Parsing ────────────────────────────────────────────────────────────────

def parse_feed(xml_text: str, source_name: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  [XML error] {e}")
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for item in root.findall(".//item"):
        title = clean_html(getattr(item.find("title"),       "text", "") or "")
        link  = (getattr(item.find("link"),        "text", "") or "").strip()
        desc  = clean_html(getattr(item.find("description"), "text", "") or "")
        date  = fmt_date(getattr(item.find("pubDate"),       "text", "") or "")
        if title and link:
            items.append({"title": title, "link": link,
                          "description": desc, "published": date,
                          "source": source_name})

    if not items:
        for entry in root.findall("atom:entry", ns):
            title_el   = entry.find("atom:title",   ns)
            link_el    = entry.find("atom:link",     ns)
            summary_el = entry.find("atom:summary",  ns)
            updated_el = entry.find("atom:updated",  ns)

            title = clean_html(getattr(title_el,   "text", "") or "")
            link  = ((link_el.get("href", "") if link_el is not None else "") or "").strip()
            desc  = clean_html(getattr(summary_el, "text", "") or "")
            date  = fmt_date(getattr(updated_el,   "text", "") or "")

            if title and link:
                items.append({"title": title, "link": link,
                              "description": desc, "published": date,
                              "source": source_name})
    return items


def fetch_pubmed_json(url: str, source_name: str, client: httpx.Client) -> list[dict]:
    """Fetch PubMed IDs via E-utilities JSON, then fetch summaries."""
    items = []
    try:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
        ids  = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return items
        # Fetch summaries for found IDs
        summary_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&id={','.join(ids)}&retmode=json"
        )
        sresp = client.get(summary_url)
        sresp.raise_for_status()
        sdata = sresp.json()
        for uid in ids:
            rec = sdata.get("result", {}).get(uid, {})
            title = rec.get("title", "").strip()
            if not title:
                continue
            link  = f"https://pubmed.ncbi.nlm.nih.gov/{uid}/"
            desc  = rec.get("source", "") + ". " + rec.get("sortpubdate", "")[:10]
            date  = fmt_date(rec.get("pubdate", "") or rec.get("sortpubdate", ""))
            items.append({"title": title, "link": link,
                          "description": desc.strip(), "published": date,
                          "source": source_name})
    except Exception as e:
        print(f"    [WARN] PubMed JSON error: {e}")
    return items


def fetch_fresh_articles() -> list[dict]:
    all_items: list[dict] = []
    seen: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

    with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
        for source_name, url in RSS_FEEDS:
            print(f"  Fetching {source_name}: {url[:70]}…")
            try:
                if "eutils.ncbi.nlm.nih.gov" in url:
                    items = fetch_pubmed_json(url, source_name, client)
                else:
                    resp = client.get(url)
                    resp.raise_for_status()
                    items = parse_feed(resp.text, source_name)
                added = 0
                for item in items:
                    if item["link"] not in seen:
                        seen.add(item["link"])
                        all_items.append(item)
                        added += 1
                print(f"    → {added} new items in feed")
            except Exception as e:
                print(f"    [WARN] Failed: {e}")
    return all_items


# ── Ollama Summarizer ──────────────────────────────────────────────────────────

def ai_article(raw: dict) -> str:
    """
    Write a descriptive-only summary — no conclusions, no advice, no evaluation.
    Uses Ollama running locally (free, no API key needed).
    """

    # Four opening styles — varied so pages don't share the same sentence rhythm
    openings = [
        "Open with the subject and scope of the source. Describe what was studied or reported and how it was carried out.",
        "Open with the setting or context — the type of device, procedure, or facility involved. Then describe what was examined and what was found.",
        "Open with the method or approach used in the source. Then describe the subject matter and the results as reported.",
        "Open with the specific topic the source addresses. Describe the scope of the work and the results or content as presented.",
    ]

    prompt = textwrap.dedent(f"""
        You are a medical news writer covering sterile processing and surgical instrumentation.
        Your job is to describe what a source says in enough detail that a sterile processing
        professional understands the specific content — not just the topic.

        Write a descriptive summary of 220 to 280 words based on the source below.
        Plain prose only. No bullet points, no headings, no markdown. Two or three paragraphs.

        Opening style to use: {random.choice(openings)}

        What to include:
        - The specific procedure, device, pathogen, standard, or technique the source covers
        - The study design or regulatory action type (e.g. RCT, review, FDA recall, guideline update)
        - Specific findings: numbers, rates, cycle times, temperatures, concentrations, pass/fail
          results, organism names, device categories — whatever concrete detail the source contains
        - The setting described: hospital CSSD, endoscopy unit, OR, dental clinic, laboratory, etc.
        - Any specific products, methods, or standards mentioned (ISO, AAMI, CDC guidelines, etc.)

        Rules — follow every one strictly:
        - Describe only. Never write: "practitioners should", "this highlights the need for",
          "it is important to", "departments are encouraged", or any advisory language.
        - No conclusions, no takeaways, no recommendations. Stop after describing the content.
        - Do NOT just restate the title in different words. Go deeper into what the source says.
        - Vary sentence length. Short sentences and longer ones mixed naturally.
        - Use contractions where natural: don't, it's, they've.
        - Active voice almost always.
        - Never open with: "This study", "Researchers have found", "In this article".
        - Never use: furthermore, it is worth noting, in conclusion, delve,
          leverage as a verb, comprehensive, significant as filler, crucial as filler.
        - Use specific numbers and named details from the source whenever available.

        Source title : {raw['title']}
        Source       : {raw['source']}
        Published    : {raw['published']}
        Raw abstract : {raw['description']}

        Return ONLY the descriptive text. No title, no label, no intro line, no sign-off.
    """).strip()

    try:
        resp = httpx.post(
            OLLAMA_URL,
            json={
                "model":  OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature":    0.85,
                    "top_p":          0.92,
                    "repeat_penalty": 1.1,
                    "num_predict":    400,
                },
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        if not text:
            raise ValueError("Empty response from Ollama")
        return text
    except httpx.ConnectError:
        print("    [Ollama error] Cannot connect — is Ollama running? Try: ollama serve")
        return raw.get("description") or "Summary unavailable."
    except Exception as e:
        print(f"    [Ollama error] {e}")
        return raw.get("description") or "Summary unavailable."


# ── HTML Generation ────────────────────────────────────────────────────────────

def load_env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def write_article_page(env: Environment, article: dict) -> None:
    tmpl = env.get_template("article.html")
    html = tmpl.render(**article)
    (ARTICLES_DIR / f"{article['slug']}.html").write_text(html, encoding="utf-8")


def write_index_pages(env: Environment, all_articles: list[dict], built_at: str) -> int:
    total       = len(all_articles)
    total_pages = ceil(total / PAGE_SIZE) if total else 1
    tmpl        = env.get_template("index.html")

    for page_num in range(1, total_pages + 1):
        start     = (page_num - 1) * PAGE_SIZE
        page_arts = all_articles[start : start + PAGE_SIZE]

        pagination = {
            "current":     page_num,
            "total_pages": total_pages,
            "has_prev":    page_num > 1,
            "has_next":    page_num < total_pages,
            "prev_file":   page_filename(page_num - 1),
            "next_file":   page_filename(page_num + 1),
            "pages": [
                {"num": p, "file": page_filename(p), "active": p == page_num}
                for p in range(1, total_pages + 1)
            ],
        }

        html = tmpl.render(
            articles   = page_arts,
            built_at   = built_at,
            total_all  = total,
            page_size  = PAGE_SIZE,
            start_num  = start + 1,
            pagination = pagination,
        )

        out_path = OUTPUT_DIR / page_filename(page_num)
        out_path.write_text(html, encoding="utf-8")
        print(f"  Wrote {out_path.name}  ({len(page_arts)} articles, page {page_num}/{total_pages})")

    return total_pages


def make_company_slug(name: str) -> str:
    """Convert company name to URL-safe slug."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def write_company_pages(env: Environment, built_at: str) -> int:
    """
    Generate one static HTML profile page per company in COMPANIES.
    Output: companies/<slug>.html
    Uses templates/company.html (Jinja2 template).
    """
    try:
        tmpl = env.get_template("company.html")
    except Exception as e:
        print(f"  [WARN] company.html template not found: {e}")
        return 0

    written = 0
    check_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

    for i, c in enumerate(COMPANIES, 1):
        slug      = make_company_slug(c["company_name"])
        is_verified = c["company_name"] in VERIFIED_COMPANIES

        # Split key_products into up to 3 tags
        tags = [t.strip() for t in c["key_products"].split(",")]
        tag1 = tags[0] if len(tags) > 0 else c["main_category"]
        tag2 = tags[1] if len(tags) > 1 else ""
        tag3 = tags[2] if len(tags) > 2 else ""

        # Generate a short 2-sentence description from available data
        fda_str = "holding FDA clearance for US market distribution" if c["fda_approved"] else "operating in international markets"
        desc1 = (
            f"{c['company_name']} is a {c['hq_country']}-headquartered manufacturer "
            f"specialising in {c['main_category'].lower()}, {fda_str}."
        )
        desc2 = (
            f"The company's product portfolio includes {c['key_products'].split(',')[0].strip().lower()} "
            f"and related sterile processing solutions for clinical and surgical environments."
        )

        ctx = {
            "company_name":        c["company_name"],
            "sterindex_id":        f"SI-{str(i).zfill(4)}",
            "hq_country":          c["hq_country"],
            "main_category":       c["main_category"],
            "product_tag_1":       tag1,
            "product_tag_2":       tag2,
            "product_tag_3":       tag3,
            "description_sentence_1": desc1,
            "description_sentence_2": desc2,
            "fda_approved":        c["fda_approved"],
            "is_verified":         is_verified,
            "website_url":         c["website_url"],
            "check_date":          check_date,
            "built_at":            built_at,
        }

        html = tmpl.render(**ctx)
        out  = COMPANIES_DIR / f"{slug}.html"
        out.write_text(html, encoding="utf-8")
        written += 1

    return written


def write_legal_page(env: Environment, built_at: str) -> None:
    tmpl = env.get_template("legal.html")
    html = tmpl.render(built_at=built_at)
    (OUTPUT_DIR / "legal.html").write_text(html, encoding="utf-8")
    print("  Wrote legal.html")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("\n═══════════════════════════════════")
    print("  SterIndex — build.py")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═══════════════════════════════════\n")

    built_at = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    # 1. Load existing archive
    print("► Loading archive…")
    archive      = load_archive()
    archive_urls = {a["source_url"] for a in archive}
    print(f"  {len(archive)} articles already in archive\n")

    # 2. Fetch fresh RSS
    print("► Fetching RSS feeds…")
    fresh_raw = fetch_fresh_articles()
    print(f"\n  {len(fresh_raw)} total items fetched from feeds")

    # 3. Find genuinely new articles
    # Filter to sterile tech topics only — discard off-topic articles
    TOPIC_KEYWORDS = [
        "steril", "disinfect", "decontaminat", "autoclave", "endoscop",
        "reprocess", "surgical instrument", "sterile processing", "SPD",
        "washer-disinfector", "high-level disinfection", "HLD", "biological indicator",
        "chemical indicator", "instrument cleaning", "central sterile",
        "CSSD", "operating room infection", "surgical site infection",
        "medical device", "sterilization", "antiseptic", "aseptic",
        "contamination", "infection prevention", "implant sterilization",
        "ethylene oxide", "hydrogen peroxide", "plasma sterilization",
        "steam sterilization", "instrument reprocessing", "flexible scope",
    ]

    def is_on_topic(article: dict) -> bool:
        text = (article["title"] + " " + article["description"]).lower()
        return any(kw.lower() in text for kw in TOPIC_KEYWORDS)

    fresh_raw = [r for r in fresh_raw if is_on_topic(r)]
    print(f"  {len(fresh_raw)} articles passed topic relevance filter")

    new_raw = [r for r in fresh_raw if r["link"] not in archive_urls]
    print(f"  {len(new_raw)} new articles to process\n")

    if not new_raw and not archive:
        print("✗ No articles found. Check your network or RSS URLs.")
        return

    # 4. Summarize new articles + write article pages
    env        = load_env()
    used_slugs = {a["slug"] for a in archive}
    new_articles: list[dict] = []

    if new_raw:
        print("► Generating descriptive summaries…")
        for i, raw in enumerate(new_raw, 1):
            print(f"  [{i:02d}/{len(new_raw)}] {raw['title'][:68]}…")
            slug    = unique_slug(raw["title"], used_slugs)
            content = ai_article(raw)
            article = {
                "slug":       slug,
                "title":      raw["title"],
                "source":     raw["source"],
                "source_url": raw["link"],
                "published":  raw["published"],
                "content":    content,
                "built_at":   datetime.now(timezone.utc).strftime("%B %d, %Y"),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            }
            write_article_page(env, article)
            new_articles.append(article)
            if i < len(new_raw):
                time.sleep(0.2)
    else:
        print("  No new articles — skipping Ollama summarization\n")

    # 5. Merge into archive (newest first)
    all_articles = new_articles + archive
    save_archive(all_articles)
    print(f"\n► Archive now contains {len(all_articles)} total articles")

    # 6. Write paginated index pages
    print("\n► Writing paginated index pages…")
    total_pages = write_index_pages(env, all_articles, built_at)

    # 7. Write legal page
    print("\n► Writing legal.html…")
    write_legal_page(env, built_at)

    # 8. Generate company profile pages
    print("\n► Generating company profile pages…")
    company_count = write_company_pages(env, built_at)
    print(f"  Wrote {company_count} company pages to companies/")

    print(f"\n✓ Done!")
    print(f"  {len(new_articles)} new articles added")
    print(f"  {len(all_articles)} total in archive")
    print(f"  {total_pages} index page(s) written\n")


if __name__ == "__main__":
    main()
