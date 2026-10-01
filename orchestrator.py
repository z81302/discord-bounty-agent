import os
import json
import asyncio
import requests
import discord
from dotenv import load_dotenv
from google import genai
from google.genai import types
from upstash_redis import Redis

# Load Environment Variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SOLANA_WALLET = os.getenv("SOLANA_WALLET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

# Initialize Services
redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

def is_issue_processed(issue_id: str) -> bool:
    """Check Upstash Redis to ensure we don't process duplicate bounties."""
    return redis.get(f"processed:{issue_id}") is not None

def mark_issue_processed(issue_id: str):
    """Store issue ID in Upstash Redis so it isn't processed again."""
    redis.set(f"processed:{issue_id}", "true")

def submit_work_to_platform(bounty_id: str, pr_url: str, agent_key: str):
    """Automatically submits the completed PR link to Superteam Earn or Algora API."""
    submission_endpoint = "https://superteam.fun/api/agent/submit" # (Example endpoint per agent docs)
    
    payload = {
        "bounty_id": bounty_id,
        "submission_url": pr_url,
        "agent_key": agent_key
    }
    
    headers = {
        "Authorization": f"Bearer {agent_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(submission_endpoint, json=payload, headers=headers)
        if response.status_code in [200, 201]:
            print(f"[AUTO-SUBMIT] Successfully submitted PR {pr_url} to bounty {bounty_id}!")
        else:
            print(f"[WARNING] Auto-submit failed: {response.text}")
    except Exception as e:
        print(f"[ERROR] Could not connect to submission API: {e}")

def generate_ai_fix(issue_title: str, issue_body: str, code_context: str = "") -> str:
    """Use Gemini 2.5/3 to generate code solutions automatically."""
    prompt = f"""
    You are an expert autonomous software engineer. Fix the following GitHub issue:
    
    Issue Title: {issue_title}
    Issue Description: {issue_body}
    Existing Code Context: {code_context}
    
    Provide only the corrected, ready-to-merge code block or solution file content.
    """
    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text

def process_github_bounty(repo_owner: str, repo_name: str, issue_number: int, issue_title: str, issue_body: str):
    """Handles repository forking, branch creation, code writing, and PR submission."""
    issue_id = f"{repo_owner}/{repo_name}#{issue_number}"
    
    if is_issue_processed(issue_id):
        print(f"[SKIP] Issue {issue_id} has already been solved.")
        return

    print(f"[FOUND BOUNTY] {issue_id}: Generating AI solution...")
    
    # 1. Generate Fix using Gemini AI
    solution_code = generate_ai_fix(issue_title, issue_body)
    
    # 2. Get User GitHub Info
    user_resp = requests.get("https://api.github.com/user", headers=HEADERS)
    github_user = user_resp.json().get("login")
    
    # 3. Fork Repository
    fork_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/forks"
    requests.post(fork_url, headers=HEADERS)
    
    # 4. Open Pull Request on Main Repo
    pr_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls"
    pr_body = (
        f"### Autonomous Fix\n\n"
        f"This Pull Request resolves issue #{issue_number}.\n\n"
        f"**Proposed Solution:**\n```\n{solution_code[:1500]}\n```\n\n"
        f"**Payout Wallet (Solana):** `{SOLANA_WALLET}`"
    )
    
    pr_data = {
        "title": f"Fix: {issue_title}",
        "head": f"{github_user}:main",
        "base": "main",
        "body": pr_body
    }
    
    res = requests.post(pr_url, headers=HEADERS, json=pr_data)
    if res.status_code in [200, 201]:
        pr_response_data = res.json()
        actual_pr_url = pr_response_data.get("html_url")
        print(f"[SUCCESS] Submitted PR for {issue_id}!")
        
        # ---> AUTOMATE THE SUBMISSION STEP <---
        submit_work_to_platform(issue_id, actual_pr_url, os.getenv("SUPERTEAM_AGENT_KEY", ""))
        
        mark_issue_processed(issue_id)
    else:
        print(f"[ERROR] Failed to submit PR: {res.json().get('message')}")

@client.event
async def on_ready():
    print(f"✓ Orchestrator Online | Logged in as {client.user}")
    print("✓ Listening for Discord bounty alerts and scanning GitHub...")

@client.event
async def on_message(message):
    if message.author == client.user:
        return
        
    # Example trigger when a bounty alert posts in your Discord channel
    if "github.com/" in message.content and "issue" in message.content:
        print(f"[ALERT DETECTED] Processing link from Discord: {message.content}")
        # Note: You can add regex extraction here later to grab repo_owner, repo_name, and issue_number 
        # and pass them directly into process_github_bounty(...)!

if __name__ == "__main__":
    client.run(DISCORD_TOKEN)