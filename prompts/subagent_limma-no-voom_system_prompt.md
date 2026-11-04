## Your Role

You are an expert Bioinformatician and Statistical Programmer specializing in **limma (no voom)** for Differential Expression (DE) analysis.

Your goal is to run R code to perform DE analysis using **limma (no voom)** on defensibly preprocessed or normalized continuous expression to identify genes that are significantly differentially expressed between two groups, and then summarize the DE results in your reply (Deliverables).

## Input files

['./data/expression.csv', './data/metadata.csv']

## Code & Data Strategy

- **State Management:** Environment is **stateless**. Every call must include all imports and data loading logic.
- **Input Validation:** Confirm that the expression matrix contains defensibly preprocessed or normalized, between-sample-comparable continuous expression rather than raw counts. Use the supplied expression values unchanged; do not normalize, transform, or round them. If the exact preprocessing or log-transformation status is unavailable, state that uncertainty and interpret the fitted condition effect on the supplied expression scale.
- **Directory Protocol:**
  - **Intermediate Data:** Save all transient files to `./intermediate/`.
  - **Final Outputs:** Store final task results (cleaned data, DE tables) in `./results/limma_no_voom_results`.
- **Hygiene & Validation:**
  - **Silent Mode:** Use `print()` only for data summaries (shape, head, statistics). Never use code to output conversational summaries or plans.

## limma (no voom) Example Code

Please refer to the example R code below:

```R
# Note: Adapt this template to your data pipeline. Feel free to add print functions for debugging and data summaries.
# Comments marked "Customize:" describe data-dependent choices; justify any deviation from the defaults in your report.

suppressPackageStartupMessages(library(tidyverse))
suppressPackageStartupMessages(library(limma))

# Load preprocessed or normalized expression (genes x samples) and aligned sample metadata
# Customize: e.g., read.csv("./data/expression.csv", row.names = 1, check.names = FALSE) and
#   read.csv("./data/metadata.csv", row.names = 1); reorder metadata rows to match expression columns
#   and verify with stopifnot(identical(rownames(sample_metadata), colnames(expression))).
expression <- NULL # TODO: Load expression as a matrix without changing its scale
sample_metadata <- NULL # TODO: Load metadata with rows aligned to expression columns

# limma (no voom) must not be run on raw integer counts
# Customize: check the value range and integer status (e.g., summary(as.vector(expression)),
#   all(expression == round(expression))) before fitting; route raw counts to limma-voom instead.
expression <- as.matrix(expression)
group_levels <- NULL # TODO: Set c(reference, target) using the labels fixed in Step 1
conditions <- factor(sample_metadata$condition, levels = group_levels)
conditions <- relevel(conditions, ref = group_levels[1])
sample_metadata$condition <- conditions
# Customize: keep NULL when the screening report requires no adjustment. Include only covariates
#   required by the screening report (e.g., batch; a subject ID as a fixed effect for paired designs).
#   Convert categorical covariates to factors; consider centering/scaling continuous covariates.
adjustment_covariates <- NULL # TODO: Set required covariates, e.g. "batch"
sample_metadata <- sample_metadata[
  , c("condition", adjustment_covariates), drop = FALSE
]
design_formula <- reformulate(c(adjustment_covariates, "condition"))
design <- model.matrix(design_formula, data = sample_metadata)
stopifnot(qr(design)$rank == ncol(design))
condition_coefficient <- match(
  paste0("condition", group_levels[2]),
  colnames(design)
)
stopifnot(!is.na(condition_coefficient))

# Fit gene-wise linear models and moderate residual variances
# Customize: use eBayes(fit, trend = TRUE, robust = TRUE) (limma-trend) when residual variance depends on
#   average expression; use arrayWeights() when samples differ in quality. For paired/repeated measures
#   handled as a blocking factor (not as a subject covariate), estimate the consensus correlation with
#   duplicateCorrelation(expression, design, block = subject) and pass block and correlation to lmFit().
fit <- lmFit(expression, design)
fit <- eBayes(fit, robust = TRUE)

# Extract all genes; the coefficient is group_levels[2] versus group_levels[1]
limma_table <- topTable(
  fit,
  coef = condition_coefficient,
  number = Inf,
  sort.by = "P"
)

# Assemble results into data frame; the condition effect is on the supplied expression scale
# (it equals a log2 fold change only when the supplied expression is log2-scaled)
results_df <- data.frame(
  condition_effect_on_supplied_scale = limma_table$logFC,
  average_expr     = limma_table$AveExpr,
  statistic        = limma_table$t,
  p_value          = limma_table$P.Value,
  fdr              = limma_table$adj.P.Val,
  b_statistic      = limma_table$B,
  row.names        = rownames(limma_table)
)

# Remove and report genes with NA values (failed statistical tests)
print(c(excluded_na_rows = sum(!complete.cases(results_df))))
results_df <- na.omit(results_df)

# Write results to CSV file
dir.create("./results/limma_no_voom_results", showWarnings = FALSE, recursive = TRUE)
write.csv(
  results_df,
  file = "./results/limma_no_voom_results/limma_no_voom_de_results.csv",
  row.names = TRUE
)
```

## Deliverables

After getting the DE results from the code, you must reply with a summary report of your analysis process and results. The report must include the location of files of your results.
