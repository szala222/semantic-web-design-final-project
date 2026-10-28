# Verhalenbank as an RDF knowledge graph

Group project for Semantic Web Technology (Levente Szalai and Nolan Heinrich)

The project maps the Verhalenbank (Dutch folktale database) JSON export to an OWL ontology, so stories can be searched by tale type, motif, place and time with SPARQL.

## Files

| File | What it is |
|---|---|
| `final_ontology.rdf` | The ontology (RDF/XML, made in Protégé) |
| `map_to_rdf.py` | Maps the JSON export to the ontology and writes Turtle |
| `verhalenbank_data.ttl` | Output for the first export file: ontology plus data |
| `mapping_log.txt` | Counts and skipped values from that run |
| `queries.sparql` | The competency queries |
| `requirements.txt` | Python dependencies |

## Data
Since the Verhalenbank JSONs are too large (77 MB each), we kept them out of this repository. Put them in the same folder before running the script.

## Run the script

```
python3 -m pip install -r requirements.txt
python3 map_to_rdf.py --data "20231201_verhalenbank_0.json" --ontology final_ontology.rdf --out verhalenbank_data.ttl --log mapping_log.txt
```

To map all six files, use `--data "20231201_verhalenbank_*.json"`.

The script first checks that every individual and property it uses exists in the ontology, then copies the ontology into the output graph and adds the data. Load only the output `.ttl` into Fuseki or Protégé.


## Query with Fuseki
From the Fuseki folder:

```
java -jar fuseki-server.jar --file /path/to/verhalenbank_data.ttl /verhalenbank
```

Open http://localhost:3030/#/dataset/verhalenbank/query and run the queries from `queries.sparql`. Results on the first file:

1. Tales of magic recorded in Friesland: 44
2. Ogre-motif stories recorded in Limburg before 1950: 7
3. Stories per province: Friesland 408, Limburg 163, Noord-Holland 134, ...
4. Stories per decade: 26 decades
5. Stories recorded in the Netherlands that mention Germany: 11

Fuseki does not have a reasoner by, so the place queries follow the hierarchy with the property path `schema:containedInPlace*`.

## Vocabularies

- Dublin Core Terms: http://purl.org/dc/terms/
- Schema.org: https://schema.org/
