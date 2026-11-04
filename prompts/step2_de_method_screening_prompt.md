# DE Method Screening Task

## Input Materials

**Available Files**:  
['./data/expression.csv', './data/metadata.csv']

**Context**:
Review the previous Data Cleaning Report for initial insights.

## Your task in this step

Screen all candidate DE methods using the standardized data. Execute code to inspect the expression matrix and every metadata column, including potential adjustment covariates, before making decisions. Apply the **DE Method Screening Criteria** strictly.

The goal is not to choose the only one best method, but to reject statistically inappropriate methods based on the **DE Method Screening Criteria**, for example, those that probably fail to control FDR, assume distributions that do not fit the data, or do not support the input/design.

When exploring data or evaluating assumptions, generate diagnostic plots when they materially support method screening or quality assessment. Each plot should include a descriptive title and necessary axis labels, legends, or annotations. Save plots with descriptive filenames in a directory appropriate to their purpose, and reference the actual path relative to the session working directory, for example `<image path="intermediate/pca.png" />`.

## Deliverables for this step

Respond with a highly concise **DE Method Screening Report** containing 2 sections:

1. **Data Observations**: A summary of the data's dimensions and all of the diagnostics you explored. Include referenced diagnostic plots when needed.

2. **Qualified (Selected) Method(s)**: A table listing all DE methods (DESeq2, edgeR, Wilcoxon Rank-Sum Test, limma-voom, limma (no voom), NOISeq), and the statistical/logical reason for selection / rejection. Columns are "DE Method", "Qualified?" (✅/❌), "Triggering Criterion", "Detailed Explanation", "Notes for Execution".