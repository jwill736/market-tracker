from market_tracker import insiders


def test_routine_needs_all_three_prior_years():
    hist = {2025: {"doe jane"}, 2024: {"doe jane", "roe rick"}, 2023: {"doe jane"}}
    assert insiders.routine_years("2026-03-12", hist, "DOE JANE A") == [2025, 2024, 2023]
    assert insiders.routine_years("2026-03-12", hist, "Roe Rick") == [2024]


def test_classify_reads_same_month_filings(monkeypatch):
    rows = []
    for y in (2025, 2024, 2023):
        rows.append({"filingDate": f"{y}-03-15", "accessionNumber": f"0000-{y}", "primaryDocument": "x/f4.xml"})
    rows.append({"filingDate": "2024-07-01", "accessionNumber": "0000-x", "primaryDocument": "f4.xml"})

    def form4(name, code):
        return (f"<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>{name}</rptOwnerName></reportingOwnerId></reportingOwner>"
                f"<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2024-03-10</value></transactionDate>"
                f"<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding><transactionAmounts>"
                f"<transactionShares><value>100</value></transactionShares><transactionPricePerShare><value>10</value></transactionPricePerShare>"
                f"<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>"
                f"</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>")
    docs = {"0000-2025": form4("Doe Jane", "P"), "0000-2024": form4("Doe Jane", "S"), "0000-2023": form4("Doe Jane", "P")}
    insiders._cache.clear()
    r = insiders.classify("123", "DOE JANE", "2026-03-20", get=lambda url: next(v for k, v in docs.items() if k.replace("-", "") in url),
                          filings_fn=lambda c: ("Co", rows))
    assert r["kind"] == "routine" and r["years"] == [2025, 2024, 2023]
    insiders._cache.clear()
    docs["0000-2023"] = form4("Doe Jane", "A")          # a grant, not a trade
    r = insiders.classify("123", "DOE JANE", "2026-03-20", get=lambda url: next(v for k, v in docs.items() if k.replace("-", "") in url),
                          filings_fn=lambda c: ("Co", rows))
    assert r["kind"] == "opportunistic" and r["years"] == [2025, 2024]
