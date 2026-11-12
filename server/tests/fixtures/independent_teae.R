# Independent TEAE recomputation for the regression tests: base R + haven only, straight from the source XPT
# files, sharing no code with the app's R prelude or its Python reference engine.
# Usage: Rscript independent_teae.R <adsl.xpt> <adae.xpt> <out.csv>
suppressPackageStartupMessages(library(haven))
a <- commandArgs(trailingOnly = TRUE)
adsl <- as.data.frame(read_xpt(a[[1]])); adae <- as.data.frame(read_xpt(a[[2]]))
pop <- adsl[adsl$SAFFL == "Y", c("USUBJID", "TRT01A")]
N <- table(pop$TRT01A)
te <- merge(adae[adae$TRTEMFL == "Y", c("USUBJID", "AEBODSYS", "AEDECOD")], pop, by = "USUBJID")
cnt <- function(keys) { u <- unique(te[, c("USUBJID", keys)]); aggregate(USUBJID ~ ., data = u[, c(keys, "USUBJID")], FUN = length) }
anyr <- aggregate(USUBJID ~ TRT01A, data = unique(te[, c("USUBJID", "TRT01A")]), FUN = length)
soc <- cnt(c("TRT01A", "AEBODSYS")); pt <- cnt(c("TRT01A", "AEBODSYS", "AEDECOD"))
out <- rbind(data.frame(level = "any", soc = NA, pt = NA, group = anyr$TRT01A, n = anyr$USUBJID),
             data.frame(level = "soc", soc = soc$AEBODSYS, pt = NA, group = soc$TRT01A, n = soc$USUBJID),
             data.frame(level = "pt", soc = pt$AEBODSYS, pt = pt$AEDECOD, group = pt$TRT01A, n = pt$USUBJID))
out$N <- as.integer(N[out$group]); out$pct <- 100 * out$n / out$N
write.csv(out, a[[3]], row.names = FALSE)
