# LLF/LCT data attribution

CriteriaBench redistributes a deterministic subset of the Leaf Logical Forms (LLF)
annotations from the University of Washington BioNLP `leaf-corpora` repository:

- repository: https://github.com/uw-bionlp/leaf-corpora
- pinned commit: https://github.com/uw-bionlp/leaf-corpora/commit/461288aeba8b37fabd43bd7c55f0e1cb1bb10b9e
- annotation license: MIT; the complete upstream notice is in `LICENSE.upstream.txt`

The eligibility-criterion text underlying LLF derives from the LCT corpus. Cite its data
descriptor at https://doi.org/10.1038/s41597-022-01521-0 and comply with the Creative
Commons Attribution 4.0 International license:
https://creativecommons.org/licenses/by/4.0/.

The LLF annotation task and corpus use are described in the LeafAI publication:
https://pmc.ncbi.nlm.nih.gov/articles/PMC10654856/.

CriteriaBench does not correct the upstream criterion text or logical forms. It reserializes
them as inert JSONL, adds source hashes and a deterministic trial-level split, and marks three
source files that contain no logical-form body as `missing_upstream`. NCT identifiers are
retained so each criterion can be traced to its public trial record. Before byte counts,
SHA-256 lineage, and parsing, upstream text checkout CRLF or LF is canonicalized to LF;
unsupported bare carriage returns are rejected.
