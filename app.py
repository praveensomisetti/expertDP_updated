import traceback
from flask import Flask, request, jsonify, render_template
import pandas as pd
import re
import boto3
import json
import os
from dotenv import load_dotenv
import snowflake.connector

app = Flask(__name__)

# Load environment variables
load_dotenv()

# AWS credentials
aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
region_name = os.getenv("REGION_NAME")
session = boto3.Session(aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key, region_name=region_name)
bedrock_client = session.client(service_name='bedrock-runtime')

# Snowflake credentials
snowflake_user = os.getenv("SNOWFLAKE_USER")
snowflake_password = os.getenv("SNOWFLAKE_PASSWORD")
snowflake_account = os.getenv("SNOWFLAKE_ACCOUNT")

# SQL query to get dropdown data
SQL_QUERY_DROPDOWN = """
SELECT FREELANCER_ID, PROPOSAL_ID, PROPOSAL FROM PROD_DWH.SB__AI_ML.EXPERT_DYNAMIC_PROFILE_INPUT
"""

# SQL query to get detailed data based on selection
SQL_QUERY_DETAILS = """
SELECT * FROM PROD_DWH.SB__AI_ML.EXPERT_DYNAMIC_PROFILE_INPUT WHERE FREELANCER_ID = '{}' AND PROPOSAL_ID = '{}'
"""

# Prompt template
PROMPT1 = """
We have a freelancing application offering few Accounting, Bookkeeping and Tax related Services services.

In our application, clients will register and provide an overview of their work/project. Our Subject Matter Experts (SMEs) will discuss the client's use case and prepare a proposal to map the requirements to a suitable freelancer. Once our SMEs identify potential freelancers, they will express their interest by pitching based on the client's input. Alternatively, SMEs may select freelancers for interviews, recording questions and answers as `PROPOSAL_ANSWERS_TEXT`. Upon selecting a freelancer, a contract will be established, detailing the project's information, certificates, and experience.

As an SME, your task is to:

Using the information from the following text fields, consolidate all detailed summaries of the expert/freelancer to provide a clear understanding of their capabilities, qualifications, and suitability for the role. This summary should be comprehensive enough to aid in rating or hiring decisions. Avoid including task-related dates, but include relevant dates related to the expert/freelancer's qualifications, such as degree completion or certification years.

- `PITCH_TEXT`: {PITCH}
- `PROPOSAL_ANSWERS_TEXT`: {PROPOSAL_ANSWERS_TEXT}
- `PROPOSAL_TEXT`: {PROPOSAL_TEXT}
- `CONTRACT_INFO_TEXT`: {CONTRACT_INFO_TEXT}

**Note:**
1. Either `PROPOSAL_ANSWERS_TEXT` or `PITCH_TEXT` will be present.
2. Create only a SINGLE consolidated Summary by using above information and do not add any additional subheadings.
3. Do not include introductory phrases such as 'Based on the provided information.
4. The provided `CONTRACT_INFO_TEXT` contains the freelancer's previous project contract info.
5. Do not include any previous Task related dates
6. Create only A SINGLE CONSOLIDATED SUMMARY in 10 LINES
"""

# Function to connect to Snowflake and run a query
def connect_to_snowflake():
    try:
        conn = snowflake.connector.connect(
            user=snowflake_user,
            password=snowflake_password,
            account=snowflake_account
        )
        return conn
    except Exception as e:
        return None

def run_query(conn, query):
    try:
        cur = conn.cursor()
        cur.execute(query)
        df = cur.fetch_pandas_all()
        return df
    except Exception as e:
        return None

# Function to remove HTML tags from strings
def remove_tags_from_str(text):
    if text == text:  # To handle NaN values which are considered float
        tag_pattern = re.compile(r'<.*?>')
        cleaned_text = tag_pattern.sub('', text)
        return cleaned_text
    else:
        return ""

# Function to generate AI response using Bedrock
def generate_response(prompt):
    try:
        request_parameters = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens" : 1000,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                }
            ],
        }
        request_body = json.dumps(request_parameters)
        response = bedrock_client.invoke_model(
            modelId="anthropic.claude-3-5-sonnet-20240620-v1:0",
            body=request_body,
        )
        response_body = json.loads(response.get('body').read())["content"][0]["text"]
        return response_body
    except Exception as e:
        return "NA"

# Function to process each row and generate the consolidated summary
def todo_row(row):
    Pitch = remove_tags_from_str(row["PITCH"])
    Proposal_Answers = remove_tags_from_str(row["PROPOSAL_ANSWERS_TEXT"])
    Contract_info = remove_tags_from_str(row["CONTRACT_INFO_TEXT"])
    proposal = remove_tags_from_str(row["PROPOSAL"])
    prompt = PROMPT1.format(PITCH=Pitch, PROPOSAL_ANSWERS_TEXT=Proposal_Answers, PROPOSAL_TEXT=proposal, CONTRACT_INFO_TEXT=Contract_info)
    generated_text = generate_response(prompt)
    return generated_text

# Flask route for the homepage
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = connect_to_snowflake()
    if conn:
        # Fetch dropdown data from Snowflake
        df_dropdown = run_query(conn, SQL_QUERY_DROPDOWN)
        conn.close()
        if df_dropdown is not None and not df_dropdown.empty:
            # Convert dropdown data to list of dictionaries
            dropdown_data = df_dropdown.to_dict(orient='records')
        else:
            dropdown_data = []

    if request.method == 'POST':
        freelancer_id = request.form['freelancer_id']
        proposal_id = request.form['proposal_id']
        proposal = request.form['proposal']
        
        # Connect to Snowflake and run the query based on the selected dropdown values
        conn = connect_to_snowflake()
        if conn:
            df = run_query(conn, SQL_QUERY_DETAILS.format(freelancer_id, proposal_id))
            conn.close()
            if df is not None and not df.empty:
                # If a proposal is provided in the form, use it to replace the one in the DataFrame
                if proposal:
                    df['PROPOSAL'] = proposal
                df['Response'] = df.apply(todo_row, axis=1)
                response_data = df[['FREELANCER_ID', 'PROPOSAL_ID', 'Response']].to_dict(orient='records')
                return jsonify(response_data)
            else:
                return jsonify({"error": "No data found for the provided Freelancer ID and Proposal ID."})
        else:
            return jsonify({"error": "Failed to connect to Snowflake."})

    # Render the dropdown list on the frontend
    return render_template('index.html', dropdown_data=dropdown_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

