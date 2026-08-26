"""Punto de entrada del agente de Teams: python run_agent.py"""

from dotenv import load_dotenv

load_dotenv()

from agent.runner import main

main()
