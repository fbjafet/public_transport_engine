# Multimodal Public Transit Disruption & Passenger Notification Engine

Repository: `public_transport_engine`  
Branch: `feature/task-app`

## Setup (Windows PowerShell)

```powershell
cd C:\Users\fabia\Documents\public_transport_engine
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m seeds.seed_data
python run.py