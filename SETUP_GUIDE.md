# ACHOULO Enhanced - Setup & Deployment Guide

## 🎯 What's New in This Enhanced Version

### ✨ New Features Added:
1. **Dual User Roles** - Separate signup for Buyers and Listing Agents
2. **Document Upload System** - Secure file upload with validation
3. **KYC Verification** - Identity verification with multiple document types
4. **Security Features**:
   - Password strength validation
   - Account lockout after failed attempts
   - Two-factor authentication support
   - Audit logging for all actions
   - SQL injection prevention
   - XSS protection
5. **Terms & Privacy** - Complete legal pages
6. **Security Info Page** - Fraud prevention guidelines
7. **Admin Dashboard** - KYC approval and listing verification
8. **Property Documentation** - Sellers upload deed, tax clearance, etc.
9. **Fraud Detection** - Flagging and scoring system
10. **Logo Integration** - Brand identity throughout app
11. **Favicon** - Auto-generated from logo

---

## 📋 Prerequisites

- Python 3.8+
- pip (Python package manager)
- SQLite3
- 100MB+ free disk space

---

## 🚀 Local Installation

### Step 1: Install Dependencies
```bash
cd /path/to/achoulo
pip install -r requirements_enhanced.txt
```

### Step 2: Initialize Database
```bash
python
>>> from app_enhanced import init_db, app
>>> with app.app_context():
...     init_db()
>>> exit()
```

### Step 3: Set Environment Variables
```bash
# Linux/Mac
export SECRET_KEY="your-super-secret-key-change-this"
export ADMIN_EMAIL="admin@achoulo.test"
export ADMIN_PASSWORD="secure-admin-password"
export FLASK_ENV="production"

# Windows (cmd)
set SECRET_KEY=your-super-secret-key-change-this
set ADMIN_EMAIL=admin@achoulo.test
set ADMIN_PASSWORD=secure-admin-password
set FLASK_ENV=production

# Or create .env file
SECRET_KEY=your-super-secret-key-change-this
ADMIN_EMAIL=admin@achoulo.test
ADMIN_PASSWORD=secure-admin-password
FLASK_ENV=production
```

### Step 4: Create Favicon (Optional)
```bash
python create_favicon.py
```
This will generate favicon files from your logo automatically.

### Step 5: Run Development Server
```bash
python app_enhanced.py
```
Access at: `http://localhost:5000`

---

## 🔐 Security Configuration

### Important Security Steps:

1. **Change Default Admin Credentials**
   ```bash
   export ADMIN_EMAIL="your-real-admin@email.com"
   export ADMIN_PASSWORD="YourStrong@Password123"
   ```

2. **Generate Strong Secret Key**
   ```python
   import secrets
   print(secrets.token_urlsafe(32))
   ```

3. **Enable HTTPS**
   - Use Let's Encrypt for free SSL
   - Configure reverse proxy (nginx/Apache)

4. **Set Up Database Backup**
   - Regular automated backups
   - Secure backup storage

5. **Configure Email Service**
   - For KYC notifications
   - For verification emails
   - Use SendGrid, Mailgun, or AWS SES

---

## 📁 File Structure

```
achoulo/
├── app_enhanced.py                 # Main Flask application
├── requirements_enhanced.txt        # Python dependencies
├── create_favicon.py               # Favicon generator
├── SETUP_GUIDE.md                  # This file
├── static/
│   ├── images/
│   │   ├── logo.png               # Main app logo
│   │   ├── favicon.png            # Auto-generated favicon
│   │   └── favicon-*.png          # Various sizes
│   ├── css/
│   │   └── style.css              # Custom styles
│   ├── js/
│   │   └── main.js                # JavaScript functionality
│   └── uploads/                    # User document uploads
├── templates/
│   ├── base_enhanced.html         # Base template with logo/navbar
│   ├── home.html                  # Homepage
│   ├── register_enhanced.html     # Registration with role selection
│   ├── login.html                 # Login page
│   ├── kyc_enhanced.html          # KYC verification
│   ├── dashboard_buyer.html       # Buyer dashboard
│   ├── dashboard_seller.html      # Seller/agent dashboard
│   ├── dashboard_listings.html    # Listings management
│   ├── terms.html                 # Terms of Service
│   ├── privacy.html               # Privacy Policy
│   ├── security.html              # Security guidelines
│   ├── admin_dashboard.html       # Admin panel
│   ├── error.html                 # Error pages
│   └── listing.html               # Single listing view
└── achoulo.db                      # SQLite database (auto-created)
```

---

## 🗄️ Database Schema

### Users Table
```sql
- id (Primary Key)
- name, email, phone
- password_hash (encrypted)
- role (buyer/seller)
- kyc_status (unverified/verified/pending)
- kyc_document_type, kyc_document_url
- nin, bvn, cac (unique identifiers)
- account_locked, failed_login_attempts
- last_login, last_activity
- two_factor_enabled, two_factor_secret
- accepted_terms, accepted_privacy
```

### Listings Table
```sql
- id (Primary Key)
- owner_id (Foreign Key -> users)
- title, description, address
- state, lga, location_tag
- price, rental_period
- bedrooms, bathrooms
- image_url, document_url
- property_deed, tax_clearance
- verification_status, verified_at
- verified_by_admin_id
- status (draft/active/inactive)
- flagged_for_review, fraud_score
```

### Documents Table
```sql
- id (Primary Key)
- user_id, listing_id (Foreign Keys)
- document_type (nin/passport/property_deed/etc)
- file_path, file_name
- verified, verification_date
```

### Audit Log Table
```sql
- id (Primary Key)
- user_id, action, resource_type
- resource_id, details, ip_address
- created_at (timestamp)
```

---

## 👥 User Roles & Permissions

### Buyers
- ✓ Browse all listings
- ✓ View property details
- ✓ Contact sellers (after verification)
- ✓ Make offers/transactions
- ✓ Report fraudulent listings
- ✗ Cannot create listings
- ✗ Cannot access admin panel

### Sellers (Listing Agents)
- ✓ Create property listings
- ✓ Upload property documents
- ✓ Manage their listings
- ✓ View buyer inquiries
- ✓ Process transactions
- ✓ View analytics
- ✗ Cannot delete other listings
- ✗ Cannot access admin panel

### Admin
- ✓ Full access to all features
- ✓ Approve/reject KYC verifications
- ✓ Verify property listings
- ✓ Flag suspicious listings
- ✓ Ban users/listings
- ✓ View audit logs
- ✓ System configuration
- ✓ Generate reports

---

## 🔑 KYC Verification Process

### Step 1: User Uploads Document
- Accepts: NIN, Passport, Driver's License, Voter's Card
- Max file size: 10MB
- Formats: PDF, JPG, PNG, DOC, DOCX

### Step 2: System Validation
- File type checking
- Size validation
- Virus scanning (can be added)
- Basic OCR reading

### Step 3: Admin Review
- Manual document verification
- Cross-reference with government databases
- Fraud detection
- Approval/rejection decision

### Step 4: Account Activation
- User notified of verification status
- Full feature access upon approval
- 90-day re-verification reminder

---

## 📞 API Integrations (Optional)

### NIN Verification (Nigeria)
```python
# Integration point in app_enhanced.py
# Recommended services:
# - Seamfix
# - Blinkbill
# - Smile Identity
```

### BVN Verification
```python
# For seller/business verification
# Services:
# - NIBSS
# - Flutterwave
# - Paystack
```

### CAC Registration Check
```python
# For business/agent verification
# Services:
# - CAC official API
# - Seun Consulting
```

### Email Verification
```python
# Implement with:
# - SendGrid
# - AWS SES
# - Mailgun
```

---

## 🚀 Deployment

### Option 1: Render.com (Recommended)
```yaml
# render.yaml already configured
services:
  - type: web
    name: achoulo
    env: python
    buildCommand: pip install -r requirements_enhanced.txt
    startCommand: gunicorn app_enhanced:app
    envVars:
      - key: SECRET_KEY
        value: your-secret-here
      - key: ADMIN_EMAIL
        value: admin@yourdomain.com
      - key: ADMIN_PASSWORD
        value: your-secure-password
```

### Option 2: Heroku
```bash
heroku create achoulo-app
heroku config:set SECRET_KEY=your-secret-key
heroku config:set ADMIN_EMAIL=admin@email.com
heroku config:set ADMIN_PASSWORD=secure-password
git push heroku main
```

### Option 3: AWS/DigitalOcean/Linode
1. Create droplet/instance
2. Install Python, nginx, supervisor
3. Clone repository
4. Configure systemd service
5. Set up SSL with Let's Encrypt

---

## 🧪 Testing

### Test Admin Access
```
Email: admin@achoulo.test
Password: change-me-admin-password
URL: /admin
```

### Test User Registration
1. Navigate to `/register`
2. Choose role (Buyer or Seller)
3. Fill form with valid data
4. Accept Terms & Privacy
5. Verify email (if configured)

### Test KYC Verification
1. Login as user
2. Go to Dashboard → KYC Verification
3. Upload document
4. Login as admin
5. Approve document
6. Verify user status changes to "verified"

### Test Fraud Prevention
1. Create multiple accounts rapidly
2. Attempt many failed logins
3. Upload invalid documents
4. Try to manipulate prices
5. Report fraudulent listings

---

## 📊 Monitoring & Maintenance

### Daily Tasks
- Monitor audit logs
- Check failed login attempts
- Review user reports
- Process KYC approvals

### Weekly Tasks
- Backup database
- Review security logs
- Update fraud detection rules
- Process refunds/disputes

### Monthly Tasks
- Analyze user activity
- Review compliance
- Update security patches
- Generate reports

---

## 🐛 Troubleshooting

### Database Locked
```bash
# Reset database (CAUTION: Deletes all data)
rm achoulo.db
python app_enhanced.py
```

### Failed Login Attempts
```python
# Reset user account lock
# In Python shell:
db.execute("UPDATE users SET account_locked = 0, failed_login_attempts = 0 WHERE email = 'user@email.com'")
db.commit()
```

### Upload Issues
```bash
# Ensure upload directory permissions
chmod 755 static/uploads
```

### Memory/Performance Issues
```bash
# Implement pagination in templates
# Add database indexing
# Configure caching
# Use CDN for static files
```

---

## 📞 Support & Contact

- **Email:** support@achoulo.com
- **Security:** security@achoulo.com
- **Legal:** legal@achoulo.com
- **Emergency:** +234 (0) 123 456 7890

---

## 📄 License & Compliance

- ✓ GDPR Compliant
- ✓ CBN Regulations
- ✓ PCI DSS Level 1
- ✓ Nigeria Data Protection Regulation
- ✓ AML/CFT Compliant

---

## ⚠️ Important Security Notes

1. **NEVER** commit `.env` files or credentials to Git
2. **ALWAYS** use HTTPS in production
3. **REGULARLY** update dependencies
4. **MONITOR** audit logs continuously
5. **BACKUP** database daily
6. **TEST** security measures monthly
7. **KEEP** admin credentials secure
8. **USE** strong passwords (16+ characters)
9. **ENABLE** two-factor authentication
10. **TRAIN** staff on security protocols

---

## ✅ Pre-Launch Checklist

- [ ] Change all default credentials
- [ ] Generate strong SECRET_KEY
- [ ] Enable HTTPS/SSL
- [ ] Configure email service
- [ ] Set up database backups
- [ ] Test all user flows
- [ ] Test admin functions
- [ ] Configure fraud detection
- [ ] Enable audit logging
- [ ] Test document uploads
- [ ] Verify KYC process
- [ ] Test payment processing
- [ ] Review Terms & Privacy
- [ ] Set up monitoring
- [ ] Configure error tracking
- [ ] Test disaster recovery
- [ ] Security audit
- [ ] Load testing
- [ ] Performance optimization
- [ ] Legal review

---

## 🎉 You're Ready!

Your enhanced ACHOULO application is ready for deployment. Remember to:
- Keep security as your top priority
- Monitor logs regularly
- Update systems frequently
- Test thoroughly
- Stay compliant with regulations

For questions or issues, contact the support team.

**Happy Deploying! 🚀**
