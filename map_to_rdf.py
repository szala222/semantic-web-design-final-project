#!/usr/bin/env python3
"""
Map Verhalenbank JSON exports to our ontology (Turtle output).

Per story (subset = has an AT/ATU code in `subject` OR a `motif`):
  :<identifier> a :Story
  dcterms:identifier  "<identifier>"
  :hasTaleType        one of the 7 ATU range individuals   (ATU preferred over AT)
  :hasMotif           one of the 23 Thompson chapter individuals
  :hasSubgenre        subgenre individual
  :date               xsd:dateTime, first date in the free-text `date` field
  :startDate/:endDate xsd:dateTime, from date_start / date_end
  :wordCount          xsd:int
  dcterms:creator / :hasCollector / dcterms:language / :hasSourceType / :hasNamedEntity   labelled nodes
  :recordedIn         most specific place (City -> Municipality -> Province_or_Region -> Country via schema:containedInPlace)
  :mentionsPlace      Named_Entity_Location node per place name in the text

Usage:
  python3 map_to_rdf.py --data "20231201_verhalenbank_*.json" \
                       --ontology final_ontology.rdf \
                       --out verhalenbank_data.ttl --log mapping_log.txt

The script reads the ontology file to check that every individual we link to exists,
then copies the ontology into the output. Load only the output .ttl into Fuseki.
"""
import argparse
import collections
import datetime as dt
import glob
import json
import re
import sys
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import DCTERMS, RDFS, XSD


# Ontology names: we need to change them here if we rename things in Protégé.

NS = Namespace("http://www.semanticweb.org/nolanheinrich/ontologies/2026/9/SWT_Nolan_and_Levente#")
SCHEMA = Namespace("https://schema.org/")

CLASS_STORY = NS["Story"]
CLASS_TALE_TYPE = NS["Tale_Type"]
CLASS_MOTIF = NS["Motif"]
CLASS_SUBGENRE = NS["Subgenre"]

PROP_TALE_TYPE = NS["hasTaleType"]
PROP_MOTIF = NS["hasMotif"]
PROP_SUBGENRE = NS["hasSubgenre"]
PROP_START = NS["startDate"]
PROP_END = NS["endDate"]
PROP_DATE = NS["date"]
PROP_WORD_COUNT = NS["wordCount"]

# Fields that become their own nodes (JSON field, property from the story, class of the node)
CLASS_CREATOR = NS["Creator"]
CLASS_COLLECTOR = NS["Collector"]
CLASS_LANGUAGE = DCTERMS["LinguisticSystem"]
CLASS_SOURCE_TYPE = NS["Source_Type"]
CLASS_NAMED_ENTITY = NS["Named_Entity"]

PROP_CREATOR = DCTERMS["creator"]
PROP_COLLECTOR = NS["hasCollector"]
PROP_LANGUAGE = DCTERMS["language"]  
PROP_SOURCE_TYPE = NS["hasSourceType"]
PROP_NAMED_ENTITY = NS["hasNamedEntity"]

# Locations: one node per place, chained with schema:containedInPlace (locality -> municipality -> province -> country)
CLASS_PLAATS = SCHEMA["City"]
CLASS_MUNICIPALITY = NS["Municipality"]
CLASS_PROVINCE = NS["Province_or_Region"]
CLASS_COUNTRY = SCHEMA["Country"]
CLASS_NAMED_ENTITY_LOCATION = NS["Named_Entity_Location"]
PROP_RECORDED_IN = NS["recordedIn"]
PROP_IS_LOCATED = SCHEMA["containedInPlace"]
PROP_MENTIONS_PLACE = NS["mentionsPlace"]

# JSON field as class, from the most general level to the most specific
LOCATION_LEVELS = [
    ("country", CLASS_COUNTRY),
    ("administrative_area_level_1", CLASS_PROVINCE),
    ("administrative_area_level_2", CLASS_MUNICIPALITY),
    ("locality", CLASS_PLAATS),
]

LINKED_FIELDS = [
    ("creator", PROP_CREATOR, CLASS_CREATOR),
    ("collector", PROP_COLLECTOR, CLASS_COLLECTOR),
    ("language", PROP_LANGUAGE, CLASS_LANGUAGE),
    ("type", PROP_SOURCE_TYPE, CLASS_SOURCE_TYPE),
    ("named_entity", PROP_NAMED_ENTITY, CLASS_NAMED_ENTITY),
    ("named_entity_location", PROP_MENTIONS_PLACE, CLASS_NAMED_ENTITY_LOCATION),
]


# ATU ranges: (first, last, individual local name)
ATU_RANGES = [
    (1, 299, "Animal_Tales"),
    (300, 749, "Tales_of_Magic"),
    (750, 849, "Religious_Tales"),
    (850, 999, "Realistic_Tales"),
    (1000, 1199, "Tales_of_the_Stupid_Ogre"),
    (1200, 1999, "Anecdotes_and_Jokes"),
    (2000, 2399, "Formula_Tales"),
]

# Thompson Motif Index: 23 chapters 
MOTIF_CHAPTERS = {
    "A": "Mythological_Motifs",
    "B": "Animals",
    "C": "Taboo",
    "D": "Magic",
    "E": "The_Dead",
    "F": "Marvels",
    "G": "Ogres",
    "H": "Tests",
    "J": "The_Wise_and_the_Foolish",
    "K": "Deceptions",
    "L": "Reversal_of_Fortune",
    "M": "Ordaining_the_Future",
    "N": "Chance_and_Fate",
    "P": "Society",
    "Q": "Rewards_and_Punishment",
    "R": "Captives_and_Fugitives",
    "S": "Unnatural_Cruelty",
    "T": "Sex",
    "U": "The_Nature_of_Life",
    "V": "Religion",
    "W": "Traits_of_Character",
    "X": "Humor",
    "Z": "Miscellaneous",
}

# Verhalenbank `subgenre` value (lowercase) -> individual
SUBGENRES = {
    "sage": "Sage",
    "mop": "Mop",
    "sprookje": "Sprookje",
    "raadsel": "Raadsel",
    "broodjeaapverhaal": "Broodjeaapverhaal",
    "legende": "Legende",
    "lied": "Lied",
    "personal narrative": "Personal_narrative",
    "personal": "Personal",
    "exempel": "Exempel",
    "mythe": "Mythe",
    "kwispel": "Kwispel",
    "limerick": "Limerick",
}

TALE_RE = re.compile(r"^(ATU|AT)\s+(\d+)") # read a tale type code like ATU 0312 out of a "subject" value



def valid_date(text):
    try:
        return dt.date.fromisoformat(text) # turns "YYYY-MM-DD" into a date object
    except (ValueError, TypeError): 
        return None         # returns None if it is not a real date



# Field normalisers
def tale_type_ranges(subject_values, log):
    """
    AT/ATU codes -> match the number to category ranges
    If the story has any ATU code, only ATU codes are used, otherwise AT codes.
    """
    atu, at = [], []
    for raw in subject_values:
        m = TALE_RE.match(str(raw).strip()) # match only for AT/ATU codes
        if m:
            (atu if m.group(1) == "ATU" else at).append(int(m.group(2)))  # append to atu or at
    numbers = atu or at   # use the ATU numbers,if there are none, use the AT numbers
    if not atu and at:
        log["tale_type_used_AT_fallback"] += 1

    ranges = set()  # set removes duplicates (get 1 Tales_of_Magic even if multiple codes appear)
    for n in numbers:
        for low, high, name in ATU_RANGES:  # go through the 7 ranges
            if low <= n <= high:    # check if n fall within one
                ranges.add(name)
                break
        else:
            log["tale_type_out_of_range"].append(n)
    return ranges


def motif_chapters(motif_values, log):
    """Motif codes: first letter of the code tells us the category."""
    chapters = set()
    for raw in motif_values:
        code = str(raw).strip().strip("[]") # some motif codes are bracketed, fixed after first run showed dropped stories because of []
        if not code:
            continue
        letter = code[0].upper() # first letter matters only
        if letter in MOTIF_CHAPTERS:
            chapters.add(MOTIF_CHAPTERS[letter])
        else:
            log["motif_unknown_letter"].append(code)
    return chapters


def subgenre_individuals(subgenre_values, log):
    """Same strcuture for subgenre, data has them lowercased, ontology starts with a capital letter.
    It can return multiple subgenres if story has more than one"""
    names = set()
    for raw in subgenre_values:
        key = str(raw).strip().lower()
        if not key:
            continue
        if key in SUBGENRES:
            names.add(SUBGENRES[key])
        else:
            log["subgenre_unknown"].append(key)
    return names

def clean_values(values):
    """Strip whitespace, drop empty strings and duplicates (order is kept)."""
    seen, cleaned = set(), []
    for raw in values:
        text = str(raw).strip()
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def node_uri(cls, label):
    """URI for a node made from a text value, e.g. Creator + 'Bakker, C.' -> #Creator_Bakker%2C_C.
    The class name in front keeps apart the same text with different roles (e.g., a person as a creator and a named entity)"""
    class_name = re.split("[#/]", str(cls))[-1]
    return URIRef(NS[quote(f"{class_name}_{label}".replace(" ", "_"), safe="")])

def story_places(doc):
    """
    Return the place chain of a story as a list of (class, label, uri), from country down to the most specific level.
    It skips the levels that are missing or empty. The URI contains the labels of all levels above it,
    so the same name in two provinces gives two different nodes (e.g. Oudega in Friesland and in Gelderland).
    """
    chain, path = [], []
    for field, cls in LOCATION_LEVELS:
        label = str(doc.get(field) or "").strip()   # these fields are plain strings, not lists
        if not label:
            continue
        path.append(label)
        chain.append((cls, label, node_uri(cls, "_".join(path))))
    return chain


def story_dates(doc, log):
    """
    Return (date, start, end) as datetime.date, can be None.
    date  = first date in the free-text `date` string (its first 10 characters).
    start / end = date_start / date_end, first 10 characters,
    a story gets both or neither. If it gets neither but has a `date` string, we only log that.
    """
    date_string = (doc.get("date") or [""])[0]
    date = valid_date(date_string[:10])
    start = valid_date((doc.get("date_start") or [""])[0][:10])
    end = valid_date((doc.get("date_end") or [""])[0][:10])
    if not (start and end):
        start = end = None
        if date_string.strip():
            log["start_end_not_appended"].append((doc["identifier"][0], date_string))
    return date, start, end


# Main
def load_docs(patterns):
    """Read all files matching the patterns and drop duplicates based on `identifier` (each story gets read once)"""
    seen, docs = set(), []
    paths = sorted({p for pat in patterns for p in glob.glob(pat)}) # find files
    if not paths:
        sys.exit(f"No data files match {patterns}")
    for path in paths:          # read files
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for doc in data["response"]["docs"]:        # de-duplicate
            ident = (doc.get("identifier") or [None])[0]
            if not ident or ident in seen:
                continue
            seen.add(ident)
            docs.append(doc)
        print(f"read {path}")
    return docs, paths


def in_subset(doc):
    """Filter for stories that have at least 1 tale type (ATU/AT) or Motifs (Thompson's index)"""
    has_tale = any(TALE_RE.match(str(s).strip()) for s in doc.get("subject") or [])
    has_motif = any(str(m).strip() for m in doc.get("motif") or [])
    return has_tale or has_motif


def check_ontology(ontology_path):
    """Every individual and property we link to must already exist in the ontology."""
    onto = Graph()
    onto.parse(ontology_path)  # RDF/XML
    expected = (
        [(n, CLASS_TALE_TYPE) for _, _, n in ATU_RANGES]
        + [(n, CLASS_MOTIF) for n in MOTIF_CHAPTERS.values()]
        + [(n, CLASS_SUBGENRE) for n in SUBGENRES.values()]
    )
    missing = [n for n, cls in expected if (NS[n], RDF.type, cls) not in onto]
    if missing:
        sys.exit("Not found in the ontology with the expected class:\n  " + "\n  ".join(missing))
    props = [PROP_TALE_TYPE, PROP_MOTIF, PROP_SUBGENRE, PROP_START, PROP_END, PROP_DATE, PROP_WORD_COUNT]
    props += [prop for _, prop, _ in LINKED_FIELDS]
    props += [PROP_RECORDED_IN, PROP_IS_LOCATED]
    for prop in props:
        if not any(onto.triples((prop, RDF.type, None))):
            sys.exit(f"Property not declared in the ontology: {prop}")
    print("ontology check OK")


def main():
    # Command line arguments
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", nargs="+", required=True, help="JSON file(s) or glob(s)")
    ap.add_argument("--ontology", required=True, help="the .rdf exported from Protégé")
    ap.add_argument("--out", default="verhalenbank_data.ttl")
    ap.add_argument("--log", default="mapping_log.txt")
    args = ap.parse_args()

    check_ontology(args.ontology) # Ontology check first
    docs, paths = load_docs(args.data) # load data

    g = Graph()    # empty collection of triples
    g.parse(args.ontology)  # start from the ontology
    g.bind("", NS, override=True, replace=True)
    g.bind("dcterms", DCTERMS)
    g.bind("schema", SCHEMA)
    g.bind("xsd", XSD)

    log = collections.defaultdict(list)
    log["tale_type_used_AT_fallback"] = 0
    stats = collections.Counter()

    for doc in docs:
        if not in_subset(doc):    # skip what is not in subset
            continue
        ident = str(doc["identifier"][0]).strip()
        story = URIRef(NS[quote(ident, safe="")])
        stats["stories"] += 1

        g.add((story, RDF.type, CLASS_STORY))     # this is a story
        g.add((story, DCTERMS.identifier, Literal(ident)))  # its identifier here

        ranges = tale_type_ranges(doc.get("subject") or [], log)
        for name in sorted(ranges):
            g.add((story, PROP_TALE_TYPE, NS[name]))
        stats["with_tale_type"] += bool(ranges)     # increase the counter if story has a tale type
        stats["multi_range_stories"] += len(ranges) > 1  # same for stories with at least 2 tale types

        # Same as tale type for motifs
        chapters = motif_chapters(doc.get("motif") or [], log)
        for name in sorted(chapters):
            g.add((story, PROP_MOTIF, NS[name]))
        stats["with_motif"] += bool(chapters)

        # Subgenres
        subs = subgenre_individuals(doc.get("subgenre") or [], log)
        for name in sorted(subs):
            g.add((story, PROP_SUBGENRE, NS[name]))
        stats["with_subgenre"] += bool(subs)

        # Dates
        date, start, end = story_dates(doc, log)
        if date:
            g.add((story, PROP_DATE, Literal(date.isoformat() + "T00:00:00", datatype=XSD.dateTime))) # stored as dateTime with midnight as the time, to match the ontology range
        if start:
            g.add((story, PROP_START, Literal(start.isoformat() + "T00:00:00", datatype=XSD.dateTime)))
        if end:
            g.add((story, PROP_END, Literal(end.isoformat() + "T00:00:00", datatype=XSD.dateTime)))
        stats["with_date"] += bool(date)
        stats["with_start_end"] += bool(start and end)

        word_count = (doc.get("word_count") or [None])[0]
        if word_count is not None:
            g.add((story, PROP_WORD_COUNT, Literal(int(word_count), datatype=XSD.int))) # XSD.int matches the range in protege
        stats["with_word_count"] += word_count is not None

        # creator, collector, language, source type, named entities: one node per distinct text
        for field, prop, cls in LINKED_FIELDS:
            labels = clean_values(doc.get(field) or [])
            for label in labels:
                node = node_uri(cls, label)         # create a node for each text value
                g.add((node, RDF.type, cls))
                g.add((node, RDFS.label, Literal(label)))
                g.add((story, prop, node))
            stats[f"with_{field}"] += bool(labels)

        # Locations: one node per place, each linked to the level above, the story links to the most specific one
        chain = story_places(doc)
        parent = None
        for cls, label, uri in chain:
            g.add((uri, RDF.type, cls))
            g.add((uri, RDFS.label, Literal(label)))
            if parent is not None:
                g.add((uri, PROP_IS_LOCATED, parent))
            parent = uri
        if chain:
            g.add((story, PROP_RECORDED_IN, chain[-1][2]))
        stats["with_recorded_in"] += bool(chain)

    g.serialize(destination=args.out, format="turtle") # write everything in the graph to the .ttl file

    # Build report
    lines = [f"files: {', '.join(paths)}", f"documents read (unique identifiers): {len(docs)}"] # file names and how many unique entries
    lines += [f"{k}: {v}" for k, v in stats.items()]    # the counters
    lines.append(f"triples: {len(g)}")      # triple count
    for key, value in log.items(): # go through logs 
        if isinstance(value, list):  # log values are either lists (examples) or plain counts
            lines.append(f"{key}: {len(value)}")    # list holds the "problematic stories" e.g., no start, end date (this line hold the counts)
            lines += [f"    {v}" for v in value[:50]]  # show examples, maximum the first 50 items
        else:
            lines.append(f"{key}: {value}") # plain values
    # Write and print the report
    report = "\n".join(lines)
    with open(args.log, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(report)
    print(f"\nwritten: {args.out}, {args.log}")


if __name__ == "__main__":
    main()
