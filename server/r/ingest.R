# Parse an uploaded dataset (CSV, SAS XPT, RDS) into the workspace's canonical form:
#   data.rds      the parsed data frame, which R programs read through tp_read()
#   data.csv      a plain copy (blank = missing) for the independent Python reference engine
#   profile.json  schema, labels, missingness and top values for the data explorer
# Usage: Rscript ingest.R <input> <format: csv|xpt|rds> <out_dir>
suppressPackageStartupMessages({
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3) stop("usage: ingest.R <input> <format> <out_dir>")
input <- args[[1]]; fmt <- tolower(args[[2]]); out <- args[[3]]
dir.create(out, recursive = TRUE, showWarnings = FALSE)

fail <- function(msg) {
  write_json(list(ok = FALSE, error = msg), file.path(out, "profile.json"), auto_unbox = TRUE)
  cat(msg, "\n", file = stderr())
  quit(status = 2)
}

df <- tryCatch(
  switch(fmt,
    csv = readr::read_csv(input, show_col_types = FALSE, guess_max = 10000, progress = FALSE),
    xpt = haven::read_xpt(input),
    rds = readRDS(input),
    stop("unsupported format: ", fmt)
  ),
  error = function(e) fail(conditionMessage(e))
)
if (!is.data.frame(df)) fail(sprintf("the file holds a %s, not a data frame", class(df)[1]))
df <- as.data.frame(df, stringsAsFactors = FALSE)
if (ncol(df) == 0) fail("the file has no columns")

col_type <- function(x) {
  if (inherits(x, "Date")) return("date")
  if (inherits(x, "POSIXt")) return("datetime")
  if (is.factor(x)) return("factor")
  if (is.logical(x)) return("logical")
  if (is.integer(x)) return("integer")
  if (is.numeric(x)) return("numeric")
  "character"
}
is_missing <- function(x) if (is.character(x)) is.na(x) | trimws(x) == "" else is.na(x)

profile_col <- function(name) {
  x <- df[[name]]
  miss <- is_missing(x)
  vals <- as.character(x[!miss])
  tops <- head(sort(table(vals), decreasing = TRUE), 6)
  num <- is.numeric(x) && !inherits(x, c("Date", "POSIXt"))
  label <- attr(x, "label", exact = TRUE)
  list(
    name = name,
    label = if (is.null(label)) "" else as.character(label),
    type = col_type(x),
    n_missing = sum(miss),
    n_distinct = length(unique(vals)),
    min = if (num && any(!miss)) min(x, na.rm = TRUE) else NULL,
    max = if (num && any(!miss)) max(x, na.rm = TRUE) else NULL,
    top = lapply(names(tops), function(v) list(value = v, n = as.integer(tops[[v]])))
  )
}

# The CSV copy: dates as ISO strings, missing as blank, labels dropped
plain <- df
for (nm in names(plain)) {
  x <- plain[[nm]]
  if (inherits(x, c("Date", "POSIXt"))) plain[[nm]] <- format(x)
  else if (is.factor(x)) plain[[nm]] <- as.character(x)
  else if (is.numeric(x)) plain[[nm]] <- ifelse(is.na(x), NA, format(x, digits = 15, trim = TRUE, scientific = FALSE))
  attr(plain[[nm]], "label") <- NULL
}
utils::write.csv(plain, file.path(out, "data.csv"), row.names = FALSE, na = "")
saveRDS(df, file.path(out, "data.rds"))

dslabel <- attr(df, "label", exact = TRUE)
write_json(
  list(
    ok = TRUE,
    nrow = nrow(df), ncol = ncol(df),
    label = if (is.null(dslabel)) "" else dslabel,
    columns = lapply(names(df), profile_col)
  ),
  file.path(out, "profile.json"), auto_unbox = TRUE, null = "null", digits = NA
)
cat("ok", nrow(df), ncol(df), "\n")
