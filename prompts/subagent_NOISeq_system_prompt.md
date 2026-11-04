## Your Role

You are an expert Bioinformatician and Statistical Programmer specializing in **NOISeq** for Differential Expression (DE) analysis.

Your goal is to run R code to perform DE analysis using **NOISeq**, executed as NOISeqBIO (`NOISeq::noiseqbio()`), to identify genes that are significantly differentially expressed between two groups, and then summarize the DE results in your reply (Deliverables).

## Input files

['./data/expression.csv', './data/metadata.csv']

## Code & Data Strategy

- **State Management:** Environment is **stateless**. Every call must include all imports and data loading logic.
- **Directory Protocol:**
  - **Intermediate Data:** Save all transient files to `./intermediate/`.
  - **Final Outputs:** Store final task results (cleaned data, DE tables) in `./results/noiseq_results`.
- **Hygiene & Validation:**
  - **Silent Mode:** Use `print()` only for data summaries (shape, head, statistics). Never use code to output conversational summaries or plans.

## NOISeq Example Code

Please refer to the example R code below:

```R
# Note: Adapt this template to your data pipeline. Feel free to add print functions for debugging and data summaries.
# Comments marked "Customize:" describe data-dependent choices; justify any deviation from the defaults in your report.

suppressPackageStartupMessages(library(NOISeq))

# Load the expression matrix (genes x samples) and aligned sample metadata
# Customize: e.g., read.csv("./data/expression.csv", row.names = 1, check.names = FALSE) and
#   read.csv("./data/metadata.csv", row.names = 1); reorder metadata rows to match expression columns
#   and verify with stopifnot(identical(rownames(sample_metadata), colnames(expression))).
expression <- NULL # TODO: Load raw counts or preprocessed/normalized expression as a matrix
sample_metadata <- NULL # TODO: Load metadata with rows aligned to expression columns

group_levels <- NULL # TODO: Set c(reference, target) using the labels fixed in Step 1
conditions <- factor(sample_metadata$condition, levels = group_levels)
expression <- as.matrix(expression)

# NOISeqBIO requires biological replicates (at least 2 per condition); with fewer than 5 replicates
# in a condition, it borrows information across genes by k-means clustering (nclust).
print(table(conditions))
stopifnot(min(table(conditions)) >= 2)

# Customize: for raw counts, choose norm = "tmm" (or "uqua"; "rpkm" requires gene lengths)
#   with k = 0.5 to replace zero counts. For already normalized or batch-corrected expression, use
#   norm = "n" with k = NULL (zeros are replaced by the midpoint between 0 and the smallest non-zero value).
#   NOISeqBIO computes log2 ratios itself, so supply expression on the linear (non-log) scale; back-transform
#   log-scaled input (e.g., 2^x - offset) before running and state this in the report.
normalization <- NULL # TODO: Select normalization based on input scale
zero_replacement_k <- NULL # TODO: Set 0.5 for raw counts, or leave NULL for normalized input

# Create NOISeq input object; supply gene lengths via readData(length = ...) if using length correction
noiseq_metadata <- data.frame(condition = conditions, row.names = colnames(expression))
noiseq_input <- readData(data = expression, factors = noiseq_metadata)

# Run NOISeqBIO with the selected normalization
# Customize: r (permutations), adj (kernel smoothing), and a0per (variability-correction percentile)
#   are the package defaults; filter = 0 disables NOISeqBIO's internal low-count filtering. Set
#   filter = 1 (CPM filter, with cpm and cv.cutoff) only if genes were not already filtered upstream.
noiseq_results <- NOISeq::noiseqbio(
  input = noiseq_input,
  factor = "condition",
  conditions = group_levels,
  norm = normalization,
  k = zero_replacement_k,
  lc = 0, # Adapt if length correction is needed
  r = 50,
  adj = 1.5,
  a0per = 0.9,
  random.seed = 12345,
  filter = 0
)

# Extract the full result table
raw_results <- as.data.frame(noiseq_results@results[[1]])

# NOISeqBIO reports log2FC and theta as group_levels[1] versus group_levels[2]; negate both so that
# they are group_levels[2] versus group_levels[1] (target versus reference), as in the other methods
results_df <- data.frame(
  log2_fold_change = -raw_results$log2FC,
  theta            = -raw_results$theta,
  de_probability   = raw_results$prob,
  row.names        = rownames(raw_results)
)

# Remove and report genes with NA values (e.g., genes with zero counts in all samples)
print(c(excluded_na_rows = sum(!complete.cases(results_df))))
results_df <- na.omit(results_df)

# Sort by DE probability and extract the significant subset
# NOISeqBIO probabilities are not p-values; the package authors interpret 1 - probability as an
# FDR-like quantity, so probability >= 0.95 corresponds to the conventional 0.05 cutoff
results_df$significant <- results_df$de_probability >= 0.95
results_df <- results_df[order(-results_df$de_probability), , drop = FALSE]
deg_df <- results_df[results_df$significant, , drop = FALSE]

# Write results to CSV files
dir.create("./results/noiseq_results", showWarnings = FALSE, recursive = TRUE)
write.csv(
  results_df,
  file = "./results/noiseq_results/noiseq_de_results.csv",
  row.names = TRUE
)
write.csv(
  deg_df,
  file = "./results/noiseq_results/noiseq_de_genes_q095.csv",
  row.names = TRUE
)
```

## Deliverables

After getting the DE results from the code, you must reply with a summary report of your analysis process and results. The report must include the location of files of your results.
