"""
AWS Lambda handler for Streamlit app
Bridges between AWS Lambda and Streamlit using Lambda Web Adapter
"""

import os
import sys
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add app directory to path
app_dir = Path(__file__).parent
sys.path.insert(0, str(app_dir))

# Set environment variables for Streamlit
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
os.environ["STREAMLIT_SERVER_ENABLE_CORS"] = "false"
os.environ["STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION"] = "false"
os.environ["STREAMLIT_SERVER_PORT"] = os.environ.get("PORT", "8080")
os.environ["STREAMLIT_CLIENT_LOGGER_LEVEL"] = "info"

# Import Streamlit components
import streamlit as st
from streamlit.web import cli as stcli
from streamlit.web.server import Server


def lambda_handler(event, context):
    """
    AWS Lambda handler for Streamlit app
    
    Args:
        event: Lambda event (HTTP request from API Gateway)
        context: Lambda context
        
    Returns:
        HTTP response dict
    """
    try:
        logger.info(f"Lambda handler invoked: {event.get('requestContext', {}).get('http', {}).get('method')} {event.get('rawPath', '/')}")
        
        # Start Streamlit app if not already running
        if not hasattr(lambda_handler, '_streamlit_started'):
            logger.info("Starting Streamlit app...")
            sys.argv = ["streamlit", "run", "app.py", "--logger.level=info"]
            stcli.main()
            lambda_handler._streamlit_started = True
        
        # For Lambda Web Adapter, return a simple response
        # The adapter handles forwarding to Streamlit
        return {
            "statusCode": 200,
            "body": "Streamlit app is running",
            "headers": {
                "Content-Type": "application/json"
            }
        }
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": f"Error: {str(e)}",
            "headers": {
                "Content-Type": "application/json"
            }
        }


# For local testing
if __name__ == "__main__":
    # Run Streamlit locally
    sys.argv = ["streamlit", "run", "app.py"]
    stcli.main()
