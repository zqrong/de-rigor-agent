# DE Analysis Execution Task

## Input Materials

**Available Files**:
['./data/expression.csv', './data/metadata.csv']

**Context**:
Review the previous DE Method Screening Report for the list of statistically appropriate methods and supporting evidence.

## Your task in this step

For **every** DE method selected (Qualified ✅) in the Method Screening Report, call the corresponding subagent tool to perform DE analysis. Each subagent `prompt` should include at least:  

1. The reference and target fixed in the Data Cleaning Report, with the
   comparison defined as target versus reference.  
2. The model design and coefficient or contrast to test, where applicable.
3. Any covariates that the screening report determined require adjustment.
4. The DEG significance threshold: adjusted p-value < 0.05 for methods that report p-values; NOISeqBIO DE probability ≥ 0.95 for NOISeq. Do not filter out the insignificant genes in results unless the user explicitly requests one.  
5. The input expression scale, known preprocessing history, and any method-specific settings or adjustments explicitly required by the screening decision.
6. Other necessary information and instructions.  

Call each subagent separately — do not skip any selected method.

After all subagents have returned their results, consolidate the findings and generate a final **DE Analysis Report**.

## Deliverables for this step

Respond with a **DE Analysis Report** containing the following sections:

1. **Per-Method Results**: For each selected DE method, provide a concise summary including:
   - Number of DEGs identified (up-regulated and down-regulated separately).
   - Top 10 most significant DEGs (gene name, log2FC, FDR or NOISeq DE probability as appropriate).
   - Location of the method-specific result files.

2. **Cross-Method Comparison**: Compare results across methods, including:
   - Overlap analysis: how many DEGs are shared across all methods vs. unique to individual methods. Use a Venn diagram (or similar) when 2 or more methods are involved. Reference its actual saved path relative to the session working directory, for example `<image path="results/cross_method_venn.png" />`.

3. **Interpretation**: A brief interpretation of the DE analysis results. This may include, but is not limited to, how to interpret results from different methods, whether the findings are consistent across methods, limitations of the analysis, and recommendations for next steps, if any.

4. **Output Files**: List all output file paths generated during this step.
