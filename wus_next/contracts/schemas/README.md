# Contract schema files

`contract_v1.json` is the structural JSON Schema for the eight public records. The dependency-free validator in `../validator.py` is authoritative for cross-field rules that JSON Schema alone cannot express: deadline ordering, positive intervals, completion/drop/pending states, duplicate IDs, evidence-aware profile modes, and exact LP-WUS unit interpretation.

