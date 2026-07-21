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
mcp = FastMCP("Google Marketing")

SCOPES = [
    'https://www.googleapis.com/auth/webmasters.readonly',
    'https://www.googleapis.com/auth/analytics.readonly',
    'https://www.googleapis.com/auth/analytics.edit',
    'https://www.googleapis.com/auth/tagmanager.readonly'
]
CLIENT_SECRET_FILE = os.environ.get('GOOGLE_CLIENT_SECRET_FILE', 'credentials.json')

# Token file should be in the same directory as client_secret.json
TOKEN_DIR = os.path.dirname(os.path.abspath(CLIENT_SECRET_FILE)) or '.'
TOKEN_FILE = os.path.join(TOKEN_DIR, 'token.json')

def get_credentials():
    """Authenticate and return Google Credentials."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r') as token:
                creds = Credentials.from_authorized_user_info(json.load(token), SCOPES)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Invalid token file {TOKEN_FILE}: {e}. Re-authenticating...")
            creds = None
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Warning: Failed to refresh token: {e}. Re-authenticating...")
                creds = None
        
        if not creds:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(f"OAuth Client Secret file '{CLIENT_SECRET_FILE}' not found. Please provide one or set GOOGLE_CLIENT_SECRET_FILE env var.")
            
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Ensure token directory exists
        try:
            os.makedirs(TOKEN_DIR, exist_ok=True)
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
        except IOError as e:
            print(f"Warning: Could not save token file to {TOKEN_FILE}: {e}")
            
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
def ga4_list_properties() -> str:
    """List all GA4 properties the user has access to."""
    creds = get_credentials()
    service = build('analyticsadmin', 'v1beta', credentials=creds)
    try:
        request = service.accounts().list()
        accounts_response = request.execute()
        accounts = accounts_response.get('accounts', [])
        
        if not accounts:
            return "No GA4 accounts found."
        
        result = "GA4 Accounts and Properties:\n\n"
        for account in accounts:
            account_id = account.get('name', '').split('/')[-1]
            account_display_name = account.get('displayName', 'Unknown')
            result += f"Account: {account_display_name} (ID: {account_id})\n"
            
            # List properties for this account
            properties_request = service.accounts().properties().list(parent=f"accounts/{account_id}")
            properties_response = properties_request.execute()
            properties = properties_response.get('properties', [])
            
            for prop in properties:
                prop_id = prop.get('name', '').split('/')[-1]
                prop_display_name = prop.get('displayName', 'Unknown')
                result += f"  - Property: {prop_display_name} (ID: {prop_id})\n"
        
        return result
    except Exception as e:
        return f"Error listing GA4 properties: {str(e)}"

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
        
        # Try different possible response keys
        accounts = accounts_resp.get('account', accounts_resp.get('accounts', []))
        
        if not accounts:
            return (
                "No GTM accounts found via API.\n\n"
                "IMPORTANT: This can happen if:\n"
                "1. The authenticated Google account doesn't have GTM access\n"
                "2. You need to grant GTM permissions to this Google account\n"
                "3. The GTM account is in a different Google Cloud project\n\n"
                "WORKAROUND: Use gtm_list_tags() directly with known IDs:\n"
                "  - Account: 6247347265\n"
                "  - Container: 194131688\n"
                "  - Workspace: 45\n\n"
                "These IDs can be found in GTM Admin > Container Settings"
            )
            
        result = "GTM Accounts and Containers:\n"
        for acc in accounts:
            acc_id = acc.get('accountId', acc.get('id', 'N/A'))
            acc_name = acc.get('name', 'Unknown')
            result += f"\nAccount: {acc_name} (ID: {acc_id})\n"
            
            # List containers for this account
            path = f"accounts/{acc_id}"
            containers_resp = service.accounts().containers().list(parent=path).execute()
            containers = containers_resp.get('container', containers_resp.get('containers', []))
            
            for cont in containers:
                cont_id = cont.get('containerId', cont.get('id', 'N/A'))
                cont_name = cont.get('name', 'Unknown')
                result += f"  - Container: {cont_name} (ID: {cont_id}) [Public ID: {cont.get('publicId', 'N/A')}]\n"
                
        return result
    except Exception as e:
        import traceback
        return f"Error listing GTM accounts: {str(e)}\n\nDetails:\n{traceback.format_exc()}"

@mcp.tool()
def gtm_list_tags(account_id: str, container_id: str, workspace_id: str) -> str:
    """
    List all active tracking tags in a GTM container workspace.
    Useful for Marketing to audit what is being tracked (e.g. Meta Pixel, LinkedIn Insight).
    
    Args:
        account_id: GTM Account ID (e.g., '6247347265')
        container_id: GTM Container ID - the internal numeric ID (e.g., '194131688')
        workspace_id: GTM Workspace ID (e.g., '45')
    
    Note: Use the internal numeric container ID, not the public container ID (GTM-XXXXX)
    """
    service = get_gtm_service()
    try:
        path = f"accounts/{account_id}/containers/{container_id}/workspaces/{workspace_id}"
        
        tags_resp = service.accounts().containers().workspaces().tags().list(parent=path).execute()
        tags = tags_resp.get('tag', tags_resp.get('tags', []))
        
        if not tags:
            return (
                f"No tags found in workspace {workspace_id}.\n\n"
                f"Path checked: {path}\n\n"
                "This could mean:\n"
                "1. The workspace has no tags configured\n"
                "2. Invalid account_id, container_id, or workspace_id\n"
                "3. Permission denied for this account"
            )
            
        result = f"Tags in Workspace {workspace_id} (Container: {container_id}, Account: {account_id}):\n\n"
        result += f"{'Tag Name':<40} | {'Type':<15} | {'ID':<15}\n"
        result += "-" * 75 + "\n"
        
        for tag in tags:
            tag_name = tag.get('name', 'Unknown')
            tag_type = tag.get('type', 'Unknown')
            tag_id = tag.get('tagId', tag.get('id', 'N/A'))
            result += f"{tag_name:<40} | {tag_type:<15} | {tag_id:<15}\n"
            
        return result
    except Exception as e:
        import traceback
        error_msg = str(e)
        
        # Provide helpful debugging for common errors
        if '404' in error_msg:
            return (
                f"404 Error - Not found or permission denied.\n\n"
                f"Path: accounts/{account_id}/containers/{container_id}/workspaces/{workspace_id}\n\n"
                "Please verify:\n"
                "1. Account ID is correct (numeric, e.g., 6247347265)\n"
                "2. Container ID is the internal numeric ID, NOT the public ID (GTM-XXXXX)\n"
                "3. Workspace ID is correct (usually 1 for default, or increments after publishing)\n"
                "4. Your Google account has access to this GTM account"
            )
        elif '403' in error_msg:
            return (
                f"403 Error - Forbidden. Your account doesn't have permission.\n\n"
                f"Path: accounts/{account_id}/containers/{container_id}/workspaces/{workspace_id}\n\n"
                "Try:\n"
                "1. Verify you have Editor/Admin access in GTM\n"
                "2. Re-authenticate in Claude Desktop\n"
                "3. Ensure the Google account matches the GTM account owner"
            )
        else:
            return f"Error listing GTM tags: {error_msg}\n\nDebug info:\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "9000")),
    )
