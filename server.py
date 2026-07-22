import os
import json
from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import FastMCP

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

# Initialize FastMCP server
mcp = FastMCP("Marketing Runpod")

SCOPES = [
    'https://www.googleapis.com/auth/webmasters.readonly',
    'https://www.googleapis.com/auth/analytics.readonly',
    'https://www.googleapis.com/auth/tagmanager.readonly'
]
CLIENT_SECRET_FILE = os.environ.get('GOOGLE_CLIENT_SECRET_FILE', 'credentials.json')
TOKEN_FILE = 'token.json'

def get_credentials():
    """Authenticate and return Google Credentials."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'r') as token:
            creds = Credentials.from_authorized_user_info(json.load(token), SCOPES)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(f"OAuth Client Secret file '{CLIENT_SECRET_FILE}' not found. Please provide one or set GOOGLE_CLIENT_SECRET_FILE env var.")
            
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
            
    return creds

def get_gsc_service():
    return build('searchconsole', 'v1', credentials=get_credentials())

def get_gtm_service():
    return build('tagmanager', 'v2', credentials=get_credentials())

def get_ga4_client():
    return BetaAnalyticsDataClient(credentials=get_credentials())

# ==========================================
# Google Search Console (GSC) Tools
# ==========================================

@mcp.tool()
def gsc_list_sites() -> str:
    """List all Google Search Console sites/properties the user has access to."""
    service = get_gsc_service()
    site_list = service.sites().list().execute()
    sites = site_list.get('siteEntry', [])
    
    if not sites:
        return "No sites found."
        
    result = "Sites you have access to:\n"
    for site in sites:
        url = site.get('siteUrl', 'Unknown URL')
        level = site.get('permissionLevel', 'Unknown Permission')
        result += f"- {url} (Permission: {level})\n"
    return result

@mcp.tool()
def gsc_query_analytics(site_url: str, start_date: str, end_date: str, dimensions: List[str] = None, row_limit: int = 10) -> str:
    """
    Query organic search traffic data for a specific site.
    
    Args:
        site_url: The URL of the property in Search Console (e.g., 'sc-domain:example.com' or 'https://example.com/')
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        dimensions: List of dimensions to group by (e.g., ['query', 'page', 'country', 'device', 'date'])
        row_limit: Maximum number of rows to return (default 10)
    """
    service = get_gsc_service()
    request = {
        'startDate': start_date,
        'endDate': end_date,
        'rowLimit': row_limit
    }
    if dimensions:
        request['dimensions'] = dimensions
        
    try:
        response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()
        rows = response.get('rows', [])
        
        if not rows:
            return f"No data found for {site_url} between {start_date} and {end_date}."
            
        result = f"Search Analytics Data for {site_url}:\n\n"
        dim_headers = dimensions if dimensions else []
        headers = dim_headers + ['Clicks', 'Impressions', 'CTR', 'Position']
        result += " | ".join(headers) + "\n"
        result += "-" * (len(" | ".join(headers)) + 10) + "\n"
        
        for row in rows:
            keys = row.get('keys', [])
            metrics = [
                str(row.get('clicks', 0)),
                str(row.get('impressions', 0)),
                f"{row.get('ctr', 0):.4f}",
                f"{row.get('position', 0):.2f}"
            ]
            row_data = list(keys) + metrics
            result += " | ".join(row_data) + "\n"
            
        return result
    except Exception as e:
        return f"Error querying search analytics: {str(e)}"

# ==========================================
# Google Analytics 4 (GA4) Tools
# ==========================================

@mcp.tool()
def ga4_traffic_acquisition(property_id: str, start_date: str, end_date: str, limit: int = 10) -> str:
    """
    Get user acquisition data by channel/source to see where traffic comes from.
    Useful for Marketing to analyze campaign performance.
    
    Args:
        property_id: The GA4 Property ID (e.g., '123456789')
        start_date: Start date (e.g., '2023-01-01' or '30daysAgo')
        end_date: End date (e.g., 'today')
        limit: Max rows to return
    """
    client = get_ga4_client()
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions"), Metric(name="activeUsers"), Metric(name="newUsers")],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=limit
    )
    
    try:
        response = client.run_report(request)
        result = f"Traffic Acquisition for Property {property_id}:\n\n"
        result += "Channel | Sessions | Active Users | New Users\n"
        result += "-" * 50 + "\n"
        for row in response.rows:
            result += f"{row.dimension_values[0].value} | {row.metric_values[0].value} | {row.metric_values[1].value} | {row.metric_values[2].value}\n"
        return result
    except Exception as e:
        return f"Error querying GA4: {str(e)}"

@mcp.tool()
def ga4_user_engagement(property_id: str, start_date: str, end_date: str) -> str:
    """
    Get high-level engagement metrics: Sessions, bounce rate, and average session duration.
    Useful for CEO/Marketing to gauge site health.
    """
    client = get_ga4_client()
    request = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=[
            Metric(name="sessions"), 
            Metric(name="bounceRate"), 
            Metric(name="averageSessionDuration"),
            Metric(name="screenPageViews")
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)]
    )
    
    try:
        response = client.run_report(request)
        if not response.rows:
            return "No data found."
        
        row = response.rows[0]
        return (
            f"Engagement Metrics for Property {property_id} ({start_date} to {end_date}):\n"
            f"- Total Sessions: {row.metric_values[0].value}\n"
            f"- Bounce Rate: {float(row.metric_values[1].value) * 100:.2f}%\n"
            f"- Avg Session Duration: {float(row.metric_values[2].value):.2f} seconds\n"
            f"- Total Page Views: {row.metric_values[3].value}\n"
        )
    except Exception as e:
        return f"Error querying GA4: {str(e)}"

@mcp.tool()
def ga4_conversions_and_sales(property_id: str, start_date: str, end_date: str, limit: int = 10) -> str:
    """
    Get conversion events, purchases, and total revenue.
    Useful for Sales and CEO to track ROI.
    """
    client = get_ga4_client()
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="eventName")],
        metrics=[Metric(name="conversions"), Metric(name="totalRevenue")],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=limit
    )
    
    try:
        response = client.run_report(request)
        result = f"Conversions & Sales for Property {property_id}:\n\n"
        result += "Event Name | Conversions | Total Revenue\n"
        result += "-" * 50 + "\n"
        
        total_conv = 0
        total_rev = 0.0
        
        for row in response.rows:
            event = row.dimension_values[0].value
            conv = int(row.metric_values[0].value)
            rev = float(row.metric_values[1].value)
            
            if conv > 0 or rev > 0:
                result += f"{event} | {conv} | ${rev:.2f}\n"
                total_conv += conv
                total_rev += rev
                
        result += "-" * 50 + "\n"
        result += f"TOTAL | {total_conv} | ${total_rev:.2f}\n"
        return result
    except Exception as e:
        return f"Error querying GA4: {str(e)}"

# ==========================================
# Google Tag Manager (GTM) Tools
# ==========================================

@mcp.tool()
def gtm_list_accounts_containers() -> str:
    """List all GTM accounts and their containers."""
    service = get_gtm_service()
    try:
        accounts_resp = service.accounts().list().execute()
        accounts = accounts_resp.get('account', [])
        
        if not accounts:
            return "No GTM accounts found."
            
        result = "GTM Accounts and Containers:\n"
        for acc in accounts:
            acc_id = acc['accountId']
            acc_name = acc['name']
            result += f"\nAccount: {acc_name} (ID: {acc_id})\n"
            
            # List containers for this account
            path = f"accounts/{acc_id}"
            containers_resp = service.accounts().containers().list(parent=path).execute()
            containers = containers_resp.get('container', [])
            
            for cont in containers:
                result += f"  - Container: {cont['name']} (ID: {cont['containerId']}) [Public ID: {cont.get('publicId', 'N/A')}]\n"
                
        return result
    except Exception as e:
        return f"Error listing GTM accounts: {str(e)}"

@mcp.tool()
def gtm_list_tags(account_id: str, container_id: str, workspace_id: str) -> str:
    """
    List all active tracking tags in a GTM container workspace.
    Useful for Marketing to audit what is being tracked (e.g. Meta Pixel, LinkedIn Insight).
    """
    service = get_gtm_service()
    try:
        path = f"accounts/{account_id}/containers/{container_id}/workspaces/{workspace_id}"
        tags_resp = service.accounts().containers().workspaces().tags().list(parent=path).execute()
        tags = tags_resp.get('tag', [])
        
        if not tags:
            return f"No tags found in workspace {workspace_id}."
            
        result = f"Tags in Workspace {workspace_id}:\n"
        for tag in tags:
            result += f"- {tag['name']} (Type: {tag['type']})\n"
            
        return result
    except Exception as e:
        return f"Error listing GTM tags: {str(e)}"

if __name__ == "__main__":
    import uvicorn
    from starlette.responses import PlainTextResponse
    
    # Add a simple health check at the root URL so Render and browsers don't show 404
    @mcp._app.get("/")
    async def health_check():
        return PlainTextResponse("MCP Server is running! Point your Claude config to /sse")

    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.getenv("PORT", "9000"))
    
    uvicorn.run(mcp.sse_app, host=mcp.settings.host, port=mcp.settings.port)
