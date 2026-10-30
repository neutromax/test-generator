# AWS Lambda Deployment Checklist

Quick 1-month deployment plan for Test Generator on AWS Lambda.

---

## ✅ Pre-Deployment (Today)

- [x] Repository cleaned and ready for GitHub
- [x] Dockerfile created
- [x] Lambda handler created
- [x] Streamlit config for Lambda created
- [x] Deployment guide created
- [ ] Code pushed to GitHub

### Action: Push to GitHub

```bash
cd "d:\test generator"
git add .
git commit -m "Add Lambda deployment files"
git push origin main
```

---

## ✅ AWS Setup (Week 1)

### 1. Create AWS Account / Login
- Go to https://aws.amazon.com
- Sign in to console

### 2. Create IAM Role for Lambda

```bash
aws iam create-role \
  --role-name lambda-execution-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

aws iam attach-role-policy \
  --role-name lambda-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

### 3. Create ECR Repository

```bash
aws ecr create-repository \
  --repository-name test-generator \
  --region us-east-1
```

### 4. Setup GitHub Actions (if using GitHub Actions)
- Go to GitHub repo → Settings → Secrets and variables → Actions
- Add `AWS_ROLE_ARN` with your Lambda execution role ARN

---

## ✅ Deployment (Week 2)

### Option A: GitHub Actions (Automatic)
1. Push to main branch
2. GitHub Actions builds and deploys automatically

### Option B: Manual Deployment

```bash
# 1. Get AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 2. Login to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

# 3. Build and push (requires Docker installed locally)
docker build -t test-generator:latest .
docker tag test-generator:latest \
  $AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/test-generator:latest
docker push \
  $AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/test-generator:latest

# 4. Create Lambda function
aws lambda create-function \
  --function-name test-generator-app \
  --role arn:aws:iam::$AWS_ACCOUNT_ID:role/lambda-execution-role \
  --code ImageUri=$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/test-generator:latest \
  --package-type Image \
  --timeout 300 \
  --memory-size 1024 \
  --environment Variables="{VIO_API_KEY=YOUR_KEY_HERE,VIO_API_BASE=https://vio.automotive-wan.com:446}" \
  --region us-east-1
```

### 5. Create Function URL

```bash
aws lambda create-function-url-config \
  --function-name test-generator-app \
  --auth-type NONE \
  --cors AllowOrigins=*,AllowMethods=GET,POST \
  --region us-east-1
```

This gives you a public HTTPS URL! 🎉

---

## ✅ Testing (Week 2-3)

```bash
# Get the function URL
FUNCTION_URL=$(aws lambda get-function-url-config \
  --function-name test-generator-app \
  --query 'FunctionUrl' \
  --output text \
  --region us-east-1)

# Test it
curl -I $FUNCTION_URL

# View logs
aws logs tail /aws/lambda/test-generator-app --follow
```

---

## ✅ Production (Week 3-4)

- [ ] Monitor CloudWatch logs
- [ ] Test all features in production
- [ ] Adjust memory/timeout if needed
- [ ] Set up alarms (optional)
- [ ] Document the endpoint

---

## 📊 Cost Tracking

Monthly costs should be **$5-15**:
- Lambda compute: ~$2-5
- ECR storage: ~$0.50
- Data transfer: ~$0.10
- Logs: ~$2-5

---

## 🚨 Important Notes

1. **Keep `.env` out of GitHub**
   - Already in `.gitignore` ✓
   - Use Lambda environment variables instead

2. **Set VIO API Key in Lambda**
   - Go to Lambda → Configuration → Environment variables
   - Add: `VIO_API_KEY=your_key_here`

3. **Function URL is PUBLIC**
   - Anyone with the URL can access your app
   - Consider adding authentication later

4. **Cold Start Time**
   - First request: ~10-15 seconds
   - Subsequent requests: ~1-2 seconds
   - Normal for Streamlit on Lambda

---

## 📞 Troubleshooting

### "Function not found"
```bash
aws lambda list-functions --region us-east-1
```

### "Out of Memory"
```bash
aws lambda update-function-configuration \
  --function-name test-generator-app \
  --memory-size 2048
```

### "Timeout"
```bash
aws lambda update-function-configuration \
  --function-name test-generator-app \
  --timeout 600
```

### Check logs
```bash
aws logs tail /aws/lambda/test-generator-app --follow
```

---

## ✨ After Deployment

1. **Share URL** with users
2. **Monitor** CloudWatch metrics
3. **Update** code → GitHub → Auto-redeploy
4. **Scale** based on usage
5. **Optimize** performance as needed

---

## Timeline Summary

| Week | Task | Status |
|------|------|--------|
| Week 1 | AWS Setup & IAM | Plan |
| Week 2 | Build & Deploy | Plan |
| Week 3 | Testing & Monitoring | Plan |
| Week 4 | Production Ready | Plan |

**Estimated Total Time:** 4-6 hours setup + 1 hour monitoring per week

---

**Ready to deploy? Start with the LAMBDA_DEPLOYMENT.md guide! 🚀**
