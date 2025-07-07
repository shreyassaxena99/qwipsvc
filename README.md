# QWIPSVC — FastAPI Microservice responsible for Pod Bookings and Payments

This service handles real-time booking, metered billing, and email-based access control for Qwip pods.

---

## Architecture 

```mermaid
sequenceDiagram
    participant User
    participant Frontend
    participant qwipsvc
    participant Stripe
    participant Supabase
    participant Gmail

    rect rgb(240, 240, 255)
    User ->> Frontend: Navigate to pod booking page
    Frontend ->> qwipsvc: GET /api/create-setup-intent?podName=pod1
    qwipsvc ->> Stripe: Create Customer + SetupIntent
    Stripe -->> qwipsvc: Return client_secret, customer_id
    qwipsvc -->> Frontend: Send client_secret, customer_id
    Frontend ->> Stripe: Collect card info via Elements
    end

    rect rgb(255, 255, 240)
    Stripe ->> qwipsvc: POST /webhook/stripe (setup_intent.succeeded)
    qwipsvc ->> Stripe: Retrieve customer details
    qwipsvc ->> Supabase: Get pod ID
    qwipsvc ->> Supabase: Insert session record
    qwipsvc ->> Supabase: Update pod in_use = true
    qwipsvc ->> Gmail: Send access code + end session link
    end

    rect rgb(240, 255, 240)
    User ->> Frontend: Click email link
    Frontend ->> qwipsvc: GET /api/end-session-preview?sessionId=abc
    qwipsvc ->> Supabase: Fetch session and rate
    qwipsvc -->> Frontend: Return estimated cost
    end

    rect rgb(255, 240, 240)
    User ->> Frontend: Click \"End Session\"
    Frontend ->> qwipsvc: POST /api/end-session
    qwipsvc ->> Supabase: Fetch session + pod rate
    qwipsvc ->> Stripe: Charge final amount via saved payment method
    qwipsvc ->> Supabase: Update session.end_time
    qwipsvc ->> Supabase: Update pod in_use = false
    qwipsvc -->> Frontend: Return final cost + status
    end
```


---

## Project Structure
```
qwipsvc/
├── svc/
│   └── main.py                            ← FastAPI backend
|   └── database_accessor.py               ← Supabase Accessors
|   └── email_manager.py                   ← Email Manager
|   └── env.py                             ← Environment Variable Store
|   └── models.py                          ← Pydantic Models
|   └── payments_manager.py                ← Stripe Handler
|   └── seam_accessor.py                   ← Seam Accessor for Access Code Generation (Not Implemented)
|   └── types.py                           ← Custom Types
|   └── utils.py                           ← Util Functions
├── tests/
│   └── unit                    
│       └── conftest.py                    ← Pytest Fixtures
│       └── test_email_manager.py         
│       └── test_payments_manager.py 
│       └── test_utils.py      
├── requirements.txt                       ← Dependencies
├── .env.example                           ← Sample environment config
├── .gitignore                             ← Ignore sensitive files
└── Makefile                               ← For local dev commands
```

---

## Create & Activate Python Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

---

## Install Dependencies
```bash
pip install -r requirements.txt
```

---

## Configure Environment Variables
```bash
make env
```
Fill in your values:
```
STRIPE_SECRET=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-secret-key
EMAIL_PASSWORD=your-gmail-app-password
```

---

## Start Server Locally
```bash
make service
```
Test on: `http://localhost:8000/api/create-payment-intent?podName=test`

---

## Run Unit + Integration Tests
```bash
make test
```

> :warning: Some tests are skipped unless you have a live Supabase session ID or Stripe setup.



---

## Code Formatting & Linting
```bash
make format
```

---

## Deployment
1. Push this code to a new GitHub repo named `qwipsvc`
2. Go to [railway.app](https://railway.app)
3. Click **New Project → Deploy from GitHub → Select `qwipsvc`**
4. Add environment variables via Railway dashboard
5. Done ✅

### Procfile
```
web: uvicorn svc.main:app --host 0.0.0.0 --port $$PORT
```

---

## ✅ STEP 9: (Optional) Stripe Webhook Forwarding (for local dev)
```bash
stripe listen --forward-to localhost:8000/webhook/stripe
```
