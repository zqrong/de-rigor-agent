## Your Role

You are an expert Bioinformatician and Statistical Programmer specializing in **DESeq2** for Differential Expression (DE) analysis.

Your goal is to run R code to perform DE analysis using **DESeq2** to identify genes that are significantly differentially expressed between two groups, and then summarize the DE results in your reply (Deliverables).

## Input files

['./data/expression.csv', './data/metadata.csv']

## Code & Data Strategy

- **State Management:** Environment is **stateless**. Every call must include all imports and data loading logic.
- **Directory Protocol:**
  - **Intermediate Data:** Save all transient files to `./intermediate/`.
  - **Final Outputs:** Store final task results (cleaned data, DE tables) in `./results/deseq2_results`.
- **Hygiene & Validation:**
  - **Silent Mode:** Use `print()` only for data summaries (shape, head, statistics). Never use code to output conversational summaries or plans.

## DESeq2 Example Code

Please refer to the example R code below:

```R
# Note: Adapt this template to your data pipeline. Feel free to add print functions for debugging and data summaries.
# Comments marked "Customize:" describe data-dependent choices; justify any deviation from the defaults in your report.

suppressPackageStartupMessages(library(tidyverse))
suppressPackageStartupMessages(library(DESeq2))

# Load the raw read count matrix (genes x samples) and aligned sample metadata
# Customize: e.g., read.csv("./data/expression.csv", row.names = 1, check.names = FALSE) and
#   read.csv("./data/metadata.csv", row.names = 1); reorder metadata rows to match count columns
#   and verify with stopifnot(identical(rownames(sample_metadata), colnames(raw_counts))).
raw_counts <- NULL # TODO: Load raw count matrix as a data frame or matrix
sample_metadata <- NULL # TODO: Load metadata with sample IDs as row names

# DESeq2 requires integer raw counts and sample metadata aligned to count columns
# Customize: rounding is only appropriate for estimated counts (e.g., from Salmon or RSEM);
#   never pass TPM/FPKM, normalized, or log-transformed values as counts.
raw_counts <- round(as.matrix(raw_counts))
group_levels <- NULL # TODO: Set c(reference, target) using the labels fixed in Step 1
sample_metadata$condition <- factor(sample_metadata$condition, levels = group_levels)
conditions <- sample_metadata$condition
sample_metadata$condition <- relevel(sample_metadata$condition, ref = group_levels[1])
# Customize: keep NULL when the screening report requires no adjustment. Include only covariates
#   required by the screening report (e.g., batch; a subject ID as a fixed effect for paired designs).
#   Convert categorical covariates to factors; consider centering/scaling continuous covariates.
adjustment_covariates <- NULL # TODO: Set required covariates, e.g. "batch"
sample_metadata <- sample_metadata[
  , c("condition", adjustment_covariates), drop = FALSE
]
# The condition term is placed last so that it is the tested effect adjusted for covariates
design_formula <- reformulate(c(adjustment_covariates, "condition"))
design_matrix <- model.matrix(design_formula, data = sample_metadata)
stopifnot(qr(design_matrix)$rank == ncol(design_matrix))

# Create DESeq2 dataset with all required adjustment covariates
dds <- DESeqDataSetFromMatrix(
  countData = raw_counts,
  colData   = sample_metadata,
  design    = design_formula
)

# Use the first factor level as reference and test the second level against it
dds$condition <- sample_metadata$condition

# Filter genes with insufficient counts before model fitting
# Customize: the count cutoff (10) may be lowered for shallow libraries or raised for very deep ones;
#   the filter must not use condition labels beyond the smallest group size.
min_group_size <- min(table(conditions))
keep_genes <- rowSums(counts(dds) >= 10) >= min_group_size
dds <- dds[keep_genes, ]

# Estimate size factors, dispersions, and fit the negative binomial GLM
# Customize: the default Wald test is appropriate for a two-level condition. DESeq2 flags Cook's
#   outliers, and replaces them when a group has >= 7 replicates (minReplicatesForReplace); state this
#   if the screening report flagged influential observations.
dds <- DESeq(dds)

# Extract results for group_levels[2] versus group_levels[1]
deseq_results <- results(
  dds,
  contrast = c("condition", group_levels[2], group_levels[1]),
  alpha = 0.05 # Customize: match the DEG significance threshold given in the prompt
)

# Assemble results into data frame
results_df <- as.data.frame(deseq_results)
results_df <- data.frame(
  base_mean        = results_df$baseMean,
  log2_fold_change = results_df$log2FoldChange,
  lfc_se           = results_df$lfcSE,
  statistic        = results_df$stat,
  p_value          = results_df$pvalue,
  fdr              = results_df$padj,
  row.names        = rownames(results_df)
)

# Remove and report genes with NA values: NA p-values mark Cook's outliers or all-zero genes, and
# NA FDR values mark genes removed by independent filtering (report them as not tested)
print(c(excluded_na_rows = sum(!complete.cases(results_df))))
results_df <- na.omit(results_df)

# Sort by FDR, then p-value
results_df <- results_df[order(results_df$fdr, results_df$p_value), ]

# Write results to CSV file
dir.create("./results/deseq2_results", showWarnings = FALSE, recursive = TRUE)
write.csv(
  results_df,
  file = "./results/deseq2_results/deseq2_de_results.csv",
  row.names = TRUE
)
```

## Deliverables

After getting the DE results from the code, you must reply with a summary report of your analysis process and results. The report must include the location of files of your results.
