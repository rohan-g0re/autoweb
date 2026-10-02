- connect playwright mcp
- test if it can make open browser && take actions
- can it take action based on the url and "goal" as given
- browser profile saving
- browser profile parallel spawns
- browser profile "merge profile" after each parallel spawns ends (so that profile always stays updated)


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

            2. how to take actions on broswes based on resources + business logic: Based on the "BUSINESS LOGIC" our next move is decided such as:
                - visiting meta adds and launching add campaign
                - filing for dividends and researching youtuber podcasts most aligned with our company promotion
            Hence we need to have SEPARATE documentation to do ANY OF THE POSSIBLE TASKS


