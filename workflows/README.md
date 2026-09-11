# Workflow exports

n8n workflow JSON is intentionally not committed here.

Exports embed credential IDs, webhook URLs and node parameters that map
to a specific instance. Even with credentials stripped, the export
describes the live surface of a client system in more detail than
belongs in a public repository.

The architecture those workflows implement is documented in the root
README, and the two pieces of logic worth reading are in `src/` as
standalone, tested Python.
