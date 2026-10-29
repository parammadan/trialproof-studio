# Run one program expression by expression, echoing source like the R console.
# Progress markers go to stderr as lines starting with "\036TP " so the server can show which lines are
# executing; the error (with its line range) and the session info go to TP_OUT_DIR as JSON.
# Usage (inside the sandbox): Rscript runner.R <program.R>
args <- commandArgs(trailingOnly = TRUE)
prog <- args[[1]]
out <- Sys.getenv("TP_OUT_DIR")
here <- dirname(normalizePath(sub("--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))))

mark <- function(...) cat("\036TP", ..., "\n", file = stderr())
write_status <- function(x) jsonlite::write_json(x, file.path(out, "status.json"), auto_unbox = TRUE, null = "null")

source(file.path(here, "prelude.R"))
env <- new.env(parent = globalenv())

exprs <- tryCatch(parse(prog, keep.source = TRUE), error = function(e) {
  msg <- conditionMessage(e)
  line <- suppressWarnings(as.integer(sub("^[^:]*:([0-9]+):.*$", "\\1", strsplit(msg, "\n")[[1]][1])))
  cat("Error: ", msg, "\n", sep = "", file = stderr())
  write_status(list(ok = FALSE, kind = "parse", message = msg, line = if (is.na(line)) NULL else line, end_line = NULL))
  quit(status = 1, save = "no")
})
refs <- attr(exprs, "srcref")
src <- readLines(prog, warn = FALSE)
warn_log <- list()
mark("BEGIN", length(exprs))

for (i in seq_along(exprs)) {
  l1 <- refs[[i]][1]; l2 <- refs[[i]][3]
  mark("STEP", i, l1, l2)
  code <- src[l1:l2]
  cat(paste0(c("> ", rep("+ ", length(code) - 1)), code), sep = "\n")
  res <- tryCatch(
    withCallingHandlers(
      withVisible(eval(exprs[[i]], env)),
      warning = function(w) {
        warn_log[[length(warn_log) + 1]] <<- list(line = l1, end_line = l2, message = conditionMessage(w))
        message("Warning (line ", l1, "): ", conditionMessage(w))
        invokeRestart("muffleWarning")
      }
    ),
    error = function(e) {
      cat("Error (lines ", l1, "-", l2, "): ", conditionMessage(e), "\n", sep = "", file = stderr())
      write_status(list(ok = FALSE, kind = "runtime", message = conditionMessage(e), line = l1, end_line = l2, warnings = warn_log))
      quit(status = 1, save = "no")
    }
  )
  if (res$visible) print(res$value)
}

pk <- sessionInfo()
pkgs <- c(pk$otherPkgs, pk$loadedOnly)
write_status(list(
  ok = TRUE, warnings = warn_log,
  r_version = R.version.string,
  packages = setNames(lapply(pkgs, function(p) p$Version), names(pkgs))
))
mark("END")
