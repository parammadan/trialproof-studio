# Helpers available to every TrialProof program. Sourced by runner.R before the program runs.
suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
})

.tp_data_dir <- Sys.getenv("TP_DATA_DIR")
.tp_out_dir <- Sys.getenv("TP_OUT_DIR")

# Names of the datasets in this workspace
tp_datasets <- function() list.dirs(.tp_data_dir, full.names = FALSE, recursive = FALSE)

# Read a workspace dataset by name (case-insensitive), e.g. tp_read("adsl")
tp_read <- function(name) {
  hit <- tp_datasets()[toupper(tp_datasets()) == toupper(name)]
  if (length(hit) == 0) stop(sprintf("no dataset named '%s' (have: %s)", name, paste(tp_datasets(), collapse = ", ")))
  tibble::as_tibble(readRDS(file.path(.tp_data_dir, hit[[1]], "data.rds")))
}

# Publish a result table to the Results panel.
# type = "teae_summary" tables are checked by the independent validation engine and must be in long
# form with columns: level ("any", "soc" or "pt"), soc, pt, group, n (subjects with the event),
# N (subjects in the group's denominator). pct is optional; it is recomputed for display as 100 * n / N.
tp_emit_table <- function(df, id = "table", title = id, type = "generic") {
  df <- as.data.frame(df, stringsAsFactors = FALSE)
  if (identical(type, "teae_summary")) {
    need <- c("level", "soc", "pt", "group", "n", "N")
    miss <- setdiff(need, names(df))
    if (length(miss)) stop("tp_emit_table(type = 'teae_summary') needs columns: ", paste(miss, collapse = ", "))
    bad <- setdiff(unique(df$level), c("any", "soc", "pt"))
    if (length(bad)) stop("level must be 'any', 'soc' or 'pt'; found: ", paste(bad, collapse = ", "))
  }
  for (nm in names(df)) if (inherits(df[[nm]], c("Date", "POSIXt", "factor"))) df[[nm]] <- as.character(df[[nm]])
  dir.create(file.path(.tp_out_dir, "tables"), showWarnings = FALSE, recursive = TRUE)
  jsonlite::write_json(
    list(id = id, title = title, type = type, columns = names(df), rows = df),
    file.path(.tp_out_dir, "tables", paste0(id, ".json")),
    dataframe = "rows", na = "null", auto_unbox = TRUE, digits = NA
  )
  message(sprintf("[tp] emitted table '%s' (%d rows)", id, nrow(df)))
  invisible(df)
}
