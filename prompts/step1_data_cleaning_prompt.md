# Data Cleaning Step Instruction

## Input Materials

**User Description**:
{user_message}

**Available Files**:
{user_input_files}

## Your task in this step

Execute code to inspect the available input files, identify the two groups described in the **User Description**, and create standardized analysis inputs for downstream DE analysis.

All files available for exploration are listed in **Available Files**. If a file is in a common format such as CSV, TSV, TXT, or GCT, load it directly using the appropriate parser. If the format is unclear, preview the first few lines before loading.

Identify the actual group labels corresponding to `condition_1` and `condition_2` from the user description and file contents, and fix one as the reference and the other as the target for all downstream comparisons (target versus reference). If the mapping is ambiguous or affects statistical validity, use `ask_user` before creating the final files.

Filter the data to retain only samples belonging to `condition_1` and `condition_2`. If multiple count/expression files must be merged, retain only intersecting genes unless otherwise specified.

Create exactly two output files and no others (specifications below):
- `./data/expression.csv`
- `./data/metadata.csv`

Do not create or modify any other files.

Before finishing, run final validation code to ensure both output files are correct and that the `sample_id` values in `metadata.csv` match the sample columns in `expression.csv` exactly.

1. **expression.csv** (Wide Matrix)
   - **Structure:** Rows = Genes, Columns = Samples.
   - **First Column:** `gene_id` (strictly the same as the input).
   - **Headers:** `sample_id` for the retained samples.
   - **Values:** Preserve the input expression scale. Do not normalize, transform, round, or otherwise convert the expression values in the input files.
   - **Logic:** Filter to remove samples not belonging to the interested conditions.

2. **metadata.csv** (Metadata Table)
   - **Structure:** Rows = Samples, Columns = Attributes.
   - **First Column:** `sample_id` (must be the Primary Key).
   - **Target Column:** `condition` (values must correspond to the specific groups for `condition_1` and `condition_2`).
   - **Other Columns:** Include any additional covariates if present.

## Deliverables for this step

Respond with a highly concise **Data Cleaning Report** containing 2 sections:

1. **Exploration Summary**: Brief explanation of the input formats and expression scale, and how you read and clean them. Include the two groups' labels `condition_1` and `condition_2`, explicitly identifying the reference and target.

2. **Files Created**: Names and descriptions of the two generated files.
