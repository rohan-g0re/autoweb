- [x] connect playwright mcp
- [x] test if it can make open browser && take actions
- [x] can it take action based on the url and "goal" as given
- [x] browser profile saving. `autoweb state export|inspect|verify`. Gate and re-gate
  both passed under an independent tester: logged in by hand, exported, killed
  everything, and a fresh browser seeded only from the JSON was still in the secure
  area, while an unseeded control run was redirected back to the login page.
- [x] browser profile parallel spawns. `autoweb lanes sync` generates one agent file per
  lane, each declaring its own inline MCP server so it gets its own server process, its
  own browser and its own current tab. Three independent gates, the first two of which
  failed on lanes that silently shared one browser. Proven on Arch against a real
  LinkedIn identity exported to a 1.5 MB `root.json`: one assistant message dispatched
  four lanes, a process outside the run counted four distinct `--user-data-dir` values
  held for 44 seconds, and `autoweb trace` put all four alive together for 21.6s. No
  lane drifted onto another's page, all four stayed logged in with no authwall, and
  `li_at` and `JSESSIONID` were byte-identical across `root.json` and all four lane
  files. The dispatching session was a fresh `claude -p` told nothing about lanes.
- browser profile "merge profile" after each parallel spawns ends (so that profile always stays updated)
  - Stands: the merge code exists and is tested, and a dry run against real four-lane
    output caught the merge signing `root.json` out of LinkedIn — whole-origin eviction
    fired on per-browser bot-management cookies (`__cf_bm`, `_px3`, `pxcts`,
    `__Secure-3PSIDCC`) and took it to zero origins on a successful read-only run. That
    is fixed. Owed: a merge written to `root.json`, and an identity that still works
    after it.


- command: **SIMULATION**

    1. Build:
        
        - give it a target (website(s))
        - give it resources:
            - data to fill
            - directions to navigate
        - give it goal: "how many" runs with "what directions" and "what output" needs to be achieved

        - Command specifics: we distill learning from "RUNS" into skills

    2. Testing & Eval Setup:

        - Run the simulations again based of the skill documents created
        - update the docs based on any new intel on "moves to take" AND/OR "moves to avoid"
        - remove previous bloatware from skills (if there)

    3. Directions:
        - all of these need to be .MD documents


    4. Using SIMULATION:
        - Need to do it for 2 aspects of a problem
            1. how to process the resources given based on business logic:
                Eg: you can have a business reports in pdf, excel --> how to extract them --> how does the actions taken on browser change based on the extracted data --> after extracting data from report we find out that company is in loss- the broswer actions to be taken is to start a pr campaign on meta ads --> after extracting and comapany in profit then we file for dividends on stock market for the year and reseach for top podcasts to appear on

            2. how to take actions on broswer based on resources + business logic: Based on the "BUSINESS LOGIC" our next move is decided such as:
                - visiting meta adds and launching add campaign
                - filing for dividends and researching youtuber podcasts most aligned with our company promotion
            Hence we need to have SEPARATE documentation to do ANY OF THE POSSIBLE TASKS


