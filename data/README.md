# Provided course kit

These files are the `student_kit` materials described in the assignment. They
are course-provided data and tools, not teacher output and not AI-generated.
The assignment states that `commands_pool.jsonl` is drawn from SLURP
(CC BY 4.0) and that `validation.jsonl` is for evaluation only.

If your own `student_kit` differs, copy its `data/`, `tools/` and
`network/packets_sample.jsonl` over these files; the pipeline does not depend on
any private modification.

| File | Lines / purpose | SHA-256 |
|---|---|---|
| `data/commands_pool.jsonl` | 3,000 commands (`id`, `text`) | `3470398860500fabcf360debda8fb0e7a8a6ef9f552bc0341b5f3648e335f29a` |
| `data/validation.jsonl` | 100 labelled commands (evaluation only) | `707739170f96f103cbcc41485f92cc4ddf4af903c3939ff717d1f8ccb884bd11` |
| `data/schema.json` | four keys, eight intents, slot rules | `8c47a05b3f6e0e3a4feaae2cafa570fa2976c4bb974e7f4af2f1e4433d65a656` |
| `data/rate_card.json` | hypothetical INR prices | `3bbdf210586dee2f803bdd154f6cad7630825922b8fcf489bfa37300ed731420` |
| `tools/score.py` | official scorer | `7a08da25ff23ee69c37670454752cd9217734a395f95593fb86bf2b0497f71a2` |
| `tools/locustfile.py` | official load test | `c73b121495dfeb8051f688f62a1a23c93ec8e52640d2074a11863c9f37de5196` |
| `tools/nacl_sim.py` | official NACL simulator | `a87a1c96caebd5ecb07c091b60c4e50308eb2759f86e181e2537c1f7edb3b410` |
| `network/packets_sample.jsonl` | six sample packets | `487064de091561ada281de311fd16ed63c1fea3e5422e946377399549ead8e15` |

`service/schema.json` is a copy of `data/schema.json` so the runtime image can
validate outputs without shipping the whole data directory.

The validation set is never used for training: `training/train.py` refuses
records whose ids look like validation ids and only reads the cleaned
`datasets/v1/train.jsonl` object.
