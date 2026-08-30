# PagoTotal diagnosis playbook

Start from the incident anchor. Verify whether the loss is contained in that Cell or a
child Cell. Compare sibling cells: other providers in the country, and the provider in
other countries. Compare error share and decline-code mix: elevated errors imply a
provider/infrastructure story; normal errors with shifted declines imply issuer/risk.

If the anchor loss is not explained, split it by each remaining dimension and identify a
child carrying most loss. Search lattice-related memory and consult the catalogue before
asserting provider/method capability. Select only a catalogue action whose preconditions
hold. If no unique cause is isolated, say `insufficient_evidence`, state what was checked,
and say what additional evidence is needed. Never claim the tool budget was exhausted.
