"""The worked example: a known-good specification and R program for the CDISC pilot TEAE summary.

Both were written by hand and ship with the app. They let a visitor see a run and its independent verification
without a model, and the tests use them as the reference program. They are labelled as the example wherever they
appear, never as the agent's work.
"""

SPEC = {"type": "teae_summary", "title": "TEAE summary", "datasets": {"subject": "ADSL", "events": "ADAE"},
        "subject_id": "USUBJID", "population": [{"var": "SAFFL", "op": "eq", "value": "Y"}],
        "treatment_var": "TRT01A", "event_filter": [{"var": "TRTEMFL", "op": "eq", "value": "Y"}],
        "soc_var": "AEBODSYS", "pt_var": "AEDECOD"}

TEAE_R = '''adsl <- tp_read("ADSL") %>% filter(SAFFL == "Y") %>% select(USUBJID, group = TRT01A)
denom <- adsl %>% count(group, name = "N")
ae <- tp_read("ADAE") %>% filter(TRTEMFL == "Y") %>% inner_join(adsl, by = "USUBJID")
any_ae <- ae %>% group_by(group) %>% summarise(n = n_distinct(USUBJID), .groups = "drop") %>%
  mutate(level = "any", soc = NA_character_, pt = NA_character_)
by_soc <- ae %>% group_by(group, soc = AEBODSYS) %>% summarise(n = n_distinct(USUBJID), .groups = "drop") %>%
  mutate(level = "soc", pt = NA_character_)
by_pt <- ae %>% group_by(group, soc = AEBODSYS, pt = AEDECOD) %>% summarise(n = n_distinct(USUBJID), .groups = "drop") %>%
  mutate(level = "pt")
teae <- bind_rows(any_ae, by_soc, by_pt) %>% left_join(denom, by = "group") %>% mutate(pct = 100 * n / N)
tp_emit_table(teae, id = "teae", title = "TEAEs by SOC and PT", type = "teae_summary")
'''
