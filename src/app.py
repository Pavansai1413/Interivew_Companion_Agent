import re
from urllib import response
from langchain.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.output_parsers import CommaSeparatedListOutputParser
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_community.document_loaders import PyPDFLoader
from io import BytesIO
from docx import Document
import requests
import sqlite3
import os
from dotenv import load_dotenv
import streamlit as st

load_dotenv()
API_KEY = os.getenv("API_KEY")

# Initialize the Google Gemini model
model = ChatGoogleGenerativeAI(
    google_api_key=API_KEY,
    model="gemini-2.5-flash-lite",  
    temperature=0.2
)

# Prompt and chain for extracting skills from job description
prompt = ChatPromptTemplate.from_template(
        """ You are an expert in analyzing job descriptions. Extract the key technologies, tools, 
programming languages, frameworks, and skills mentioned in the following JD. 
Output them as a comma-separated list. Ignore soft skills or generic terms. 
\n\n input: {description}"""
    )

parser = CommaSeparatedListOutputParser()

chain = prompt | model | parser


# Prompt and chain for generating interview questions and answers
qa_prompt = ChatPromptTemplate.from_template("""
Based on the extracted skills: {skills}, generate 2 interview questions and detailed answers for each skill.
Focus on practical applications and scenarios relevant to the skill.
Output as a JSON list of objects, where each object has 'skill' (skill name), 'question' (the question), and 'answer'(the detailed answer).
Example:
[
    {{"skill": "Python", 
     "question": "What is a list comprehension?", 
     "answer": "A list comprehension is a concise way to create lists in Python using a single line of code. It replaces a for loop and append operations, for example: [x**2 for x in range(5)]."}},
    ...
]                                                                                                                             
""")

qa_parser = JsonOutputParser()

qa_chain = qa_prompt | model | qa_parser


#Prompt and chain for extracting Job experience from resume
experience_prompt = ChatPromptTemplate.from_template(
    """You are an expert in parsing resumes. From the full resume text below, extract ONLY the professional experience section (e.g., work history, employment, job experiences). 
    Ignore summary, skills, education, and other sections. Output the extracted text as a single string.
    If no experience section is found, output 'No experience section found'.
    \n\n Resume: {resume_text}"""
)

experience_parser = StrOutputParser()

experience_chain = experience_prompt | model | experience_parser


#Prompt and chain for extracting keywords from experience
resume_keywords_prompt = ChatPromptTemplate.from_template(
    """You are an expert in analyzing resume experiences. Extract the key technologies, tools, 
    programming languages, frameworks, and skills mentioned in the following professional experience text. 
    Output them as a comma-separated list. Ignore soft skills or generic terms.
    \n\n Experience: {experience_text}"""
)

resume_keywords_parser = CommaSeparatedListOutputParser()

resume_keywords_chain = resume_keywords_prompt | model | resume_keywords_parser


#Prompt and chain for extracting domain details from experience
details_prompt = ChatPromptTemplate.from_template(
    """From the professional experience text below, calculate the total years of experience, infer the primary domain based on the company name (e.g., tech, finance, healthcare), 
    and list job periods as a list of tuples like [('2015', '2020'), ('2021', 'Present')].
    Output as JSON: {{{{"total_years": int, "domain": "str", "periods": list of lists}}}}.
    \n\n Experience: {experience_text}"""
)

details_parser = JsonOutputParser()

details_chain = details_prompt | model | details_parser


#Prompt and chain for generating resume points suggestions
suggestions_prompt = ChatPromptTemplate.from_template(
    """Based on the resume experience context: {experience_text}, domain: {domain}, total years: {total_years}, 
    and job periods: {periods}, suggest 2-3 bullet points to add that into resume by incorporating the missing skill '{missing_skill}'. 
    Make them relevant to the resume's context and realistic for the user's experience.
    Output as a JSON list of strings (the bullet points).
    Example: ["Led implementation of X", "Optimized Z using X"]
""")

suggestions_parser = JsonOutputParser()

suggestions_chain = suggestions_prompt | model | suggestions_parser


# Function to clean the job description text
def clean_jd(jd_text):
    cleaned_jd = jd_text.lower()
    # Remove all digits
    cleaned_jd = re.sub(r'\d+', '', cleaned_jd)
    # Remove special symbols like +
    cleaned_jd = re.sub(r'\+', '', cleaned_jd)
    # Remove hashtags
    cleaned_jd = re.sub(r'#\w+', '', cleaned_jd)
    # Remove emojis and non-ASCII symbols
    cleaned_jd = re.sub(r'[^\x00-\x7F]+', '', cleaned_jd)
    # Remove most punctuation/special characters except / and -
    cleaned_jd = re.sub(r"[!\"$%&'()*.,:;<=>?@[\\]^_`{|}~]", '', cleaned_jd)
    # Remove extra whitespace and newlines
    cleaned_jd = re.sub(r'\s+', ' ', cleaned_jd).strip()

    return cleaned_jd

# Function to parse the uploaded resume file
def parse_resume(file):
    try:
        # Load and extract text from the uploaded resume file if it's PDF or DOCX
        if file.name.endswith('.pdf'):
            with open('temp_resume.pdf', 'wb') as temp_file:
                temp_file.write(file.getvalue())
            loader = PyPDFLoader('temp_resume.pdf')
            documents = loader.load()
            text = " ".join([doc.page_content for doc in documents])
            os.remove('temp_resume.pdf')
        elif file.name.endswith('.docx'):
            doc = Document(BytesIO(file.getvalue()))
            text = " ".join([para.text for para in doc.paragraphs])
        else:
            raise ValueError("Unsupported file format. Please upload a PDF or DOCX file.")
        return text
    except Exception as e:
        raise ValueError(f"Error processing resume file: {str(e)}")
    

# Functions to store and retrieve resume experience from a local SQLite database 
def store_resume(user_id, experience_text, file_name):
    try:
        conn = sqlite3.connect('resumes.db')
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS resumes(
                  user_id TEXT PRIMARY KEY,
                  experience_text TEXT,
                  file_name TEXT)
                  ''')
        c.execute('''
            INSERT OR REPLACE INTO resumes (user_id, experience_text, file_name)
            VALUES(?,?,?)
                  ''', (user_id, experience_text, file_name))
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Error storing resume in database: {str(e)}")
    

# Function to retrieve resume experience from the local SQLite database
def retrieve_resume(user_id):
    try:
        conn = sqlite3.connect('resumes.db')
        c = conn.cursor()
        c.execute('SELECT experience_text, file_name FROM resumes WHERE user_id=?', (user_id,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else None
    except Exception as e:
        st.error(f"Error retrieving resume from database: {str(e)}")
        return None
    

# Function to find missing keywords from JD skills in resume text
def find_missing_keywords(jd_skills, resume_text):
    jd_set = set(skill.lower() for skill in jd_skills)
    resume_set = set(word.lower() for word in resume_text)
    missing_keywords = jd_set - resume_set
    return list(missing_keywords)



# Streamlit UI
st.title("Interview Companion Agent")
jd_text = st.text_area("Enter Job Description(JD) here", height=400)
# Resume Upload
st.subheader("Upload Your Resume (PDF or DOCX)")
user_id = st.text_input("Enter Your User ID", value="user123")
resume_file = st.file_uploader("Upload PDF or DOCX Resume", type=["pdf", "docx"])
if st.button("Process JD and Resume"):
    if jd_text.strip() == "":
        st.warning("Please enter a job description.")
    elif not resume_file:
        st.warning("Please upload a resume.")
    elif not user_id.strip():
        st.warning("Please enter a valid User ID.")
    else:
        # Process JD 
        cleaned_jd = clean_jd(jd_text)
        with st.spinner("Extracting skills from JD..."):
            skills = chain.invoke({"description": cleaned_jd})
        st.subheader("Extracted JD Keywords:") 
        st.write(", ".join(skills)) 
        #Process Resume
        with st.spinner("Processing Resume...."):
            try:
                resume_text = parse_resume(resume_file)
                experience_text = experience_chain.invoke({"resume_text": resume_text})
                if experience_text == 'No experience section found':
                    st.warning("No professional experience section found in the resume.")
                else:
                    store_resume(user_id, experience_text, resume_file.name)
                    st.success(f"Resume experience stored for user {user_id}!")

                    resume_keywords = resume_keywords_chain.invoke({"experience_text": experience_text})
                    st.subheader("Extracted Resume Keywords:")
                    st.write(", ".join(resume_keywords))

                    missing_keywords = find_missing_keywords(skills, resume_keywords)
                    if missing_keywords:
                        st.subheader("Missing keywords in Resume:")
                        st.write(", ".join(missing_keywords))

                        details = details_chain.invoke({"experience_text": experience_text})
                        total_years = details.get("total_years", 0)
                        domain = details.get("domain", "Unknown")
                        periods = details.get("periods", [])

                        suggestions = {}
                        for missing in missing_keywords:
                            sug = suggestions_chain.invoke({
                                "experience_text": experience_text,
                                "domain": domain,
                                "total_years": total_years,
                                "periods": periods,
                                "missing_skill": missing
                            })
                            suggestions[missing] = sug
                        st.subheader("Resume Improvement Suggestions:")
                        for kw, points in suggestions.items():
                            with st.expander(f"Skill: {kw}"):
                                for point in points:
                                    st.markdown(f"- {point}")
                    else:
                        st.info("No missing keywords-all JD skills are covered in resume experience.")

                    if skills:
                        skills_str = ", ".join(skills)
                        with st.spinner("Generating QA based on skills..."):
                            try:
                                qa_response = qa_chain.invoke({"skills": skills_str})
                            except Exception as e:
                                st.error(f"Error generating QA: {str(e)}")
                                qa_response = []

                            st.subheader("Generated Interview Questions Based on JD:")
                            if qa_response:
                                for item in qa_response:
                                    with st.expander(f"Skill: {item['skill']} - Question: {item['question']}"):
                                        st.markdown(f"**Answer:** {item['answer']}")
                            else:
                                st.write("No QA generated.")
                    else:
                        st.info("No skills extracted, so no QA generated.")
            except Exception as e:
                st.error(f"Error processing resume: {str(e)}")

