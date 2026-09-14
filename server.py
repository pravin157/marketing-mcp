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
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize FastMCP server with host='0.0.0.0' so it doesn't accidentally
# auto-enable local DNS rebinding protection (which blocks Render URLs with a 421)
mcp = FastMCP("Marketing Runpod", host="0.0.0.0")

SCOPES = [
    'https://www.googleapis.com/auth/webmasters.readonly',
    'https://www.googleapis.com/auth/analytics.readonly',
    'https://www.googleapis.com/auth/tagmanager.readonly'
]

DEFAULT_GA4_PROPERTY_ID = os.environ.get('DEFAULT_GA4_PROPERTY_ID')
DEFAULT_GSC_SITE_URL = os.environ.get('DEFAULT_GSC_SITE_URL')
DEFAULT_GTM_ACCOUNT_ID = os.environ.get('DEFAULT_GTM_ACCOUNT_ID')
DEFAULT_GTM_CONTAINER_ID = os.environ.get('DEFAULT_GTM_CONTAINER_ID')
DEFAULT_GTM_WORKSPACE_ID = os.environ.get('DEFAULT_GTM_WORKSPACE_ID')

CLIENT_SECRET_FILE = os.environ.get('GOOGLE_CLIENT_SECRET_FILE', 'credentials.json')
TOKEN_FILE = os.environ.get('GOOGLE_TOKEN_FILE', 'token.json')

def get_credentials():
    """Authenticate and return Google Credentials with support for local files and cloud env vars."""
    creds = None
    
    # 1. Check if token is passed via GOOGLE_TOKEN_JSON environment variable (e.g. on Render)
    token_json_env = os.environ.get('GOOGLE_TOKEN_JSON')
    if token_json_env:
        try:
            token_data = json.loads(token_json_env)
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except Exception as e:
            print(f"Warning: Could not parse GOOGLE_TOKEN_JSON: {e}")

    # 2. Check token file paths (local or Render Secret Files)
    token_candidates = [
        TOKEN_FILE,
        'token.json',
        '/etc/secrets/token.json'
    ]
    if not creds:
        for path in token_candidates:
            if path and os.path.exists(path):
                try:
                    with open(path, 'r') as token:
                        creds = Credentials.from_authorized_user_info(json.load(token), SCOPES)
                    if creds:
                        break
                except Exception as e:
                    print(f"Warning: Could not read token from {path}: {e}")

    # 3. Handle refresh or fallback
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Safely attempt to persist refreshed token if possible
            for path in token_candidates:
                if path and os.path.exists(path):
                    try:
                        with open(path, 'w') as token:
                            token.write(creds.to_json())
                        break
                    except Exception:
                        pass
        else:
            secret_candidates = [
                CLIENT_SECRET_FILE,
                'client_secret.json',
                'credentials.json',
                '/etc/secrets/client_secret.json',
                '/etc/secrets/credentials.json'
            ]
            secret_file = next((p for p in secret_candidates if p and os.path.exists(p)), None)
            
            # If secret file not found, check GOOGLE_CLIENT_SECRET_JSON env var
            if not secret_file and os.environ.get('GOOGLE_CLIENT_SECRET_JSON'):
                secret_file = 'client_secret.json'
                try:
                    with open(secret_file, 'w') as f:
                        f.write(os.environ['GOOGLE_CLIENT_SECRET_JSON'])
                except Exception:
                    pass

            if not secret_file or not os.path.exists(secret_file):
                raise FileNotFoundError(
                    "Google OAuth credentials/token not found. Please provide GOOGLE_TOKEN_JSON env var or upload token.json."
                )
            
            flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
            creds = flow.run_local_server(port=0)
            try:
                with open('token.json', 'w') as token:
                    token.write(creds.to_json())
            except Exception:
                pass
            
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
def gsc_query_analytics(site_url: str = None, start_date: str = None, end_date: str = None, dimensions: List[str] = None, row_limit: int = 10) -> str:
    """
    Query organic search traffic data for a specific site.
    
    Args:
        site_url: The URL of the property in Search Console (e.g., 'sc-domain:example.com' or 'https://example.com/')
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        dimensions: List of dimensions to group by (e.g., ['query', 'page', 'country', 'device', 'date'])
        row_limit: Maximum number of rows to return (default 10)
    """
    site_url = site_url or DEFAULT_GSC_SITE_URL
    if not site_url:
        return "Error: No site_url provided and no DEFAULT_GSC_SITE_URL set."
    
    if not start_date or not end_date:
        return "Error: Both start_date and end_date must be provided."
        
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
def ga4_traffic_acquisition(property_id: str = None, start_date: str = "28daysAgo", end_date: str = "today", limit: int = 10) -> str:
    """
    Get user acquisition data by channel/source to see where traffic comes from.
    Useful for Marketing to analyze campaign performance.
    
    Args:
        property_id: The GA4 Property ID (e.g., '123456789')
        start_date: Start date (e.g., '2023-01-01' or '30daysAgo')
        end_date: End date (e.g., 'today')
        limit: Max rows to return
    """
    property_id = property_id or DEFAULT_GA4_PROPERTY_ID
    if not property_id:
        return "Error: No property_id provided and no DEFAULT_GA4_PROPERTY_ID set."
        
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
def ga4_user_engagement(property_id: str = None, start_date: str = "28daysAgo", end_date: str = "today") -> str:
    """
    Get high-level engagement metrics: Sessions, bounce rate, and average session duration.
    Useful for CEO/Marketing to gauge site health.
    """
    property_id = property_id or DEFAULT_GA4_PROPERTY_ID
    if not property_id:
        return "Error: No property_id provided and no DEFAULT_GA4_PROPERTY_ID set."
        
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
def ga4_conversions_and_sales(property_id: str = None, start_date: str = "28daysAgo", end_date: str = "today", limit: int = 10) -> str:
    """
    Get conversion events, purchases, and total revenue.
    Useful for Sales and CEO to track ROI.
    """
    property_id = property_id or DEFAULT_GA4_PROPERTY_ID
    if not property_id:
        return "Error: No property_id provided and no DEFAULT_GA4_PROPERTY_ID set."
        
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

@mcp.tool()
def ga4_user_acquisition(property_id: str = None, start_date: str = "28daysAgo", 
                          end_date: str = "today", limit: int = 10) -> str:
    """
    Get user acquisition data by FIRST USER channel group (the channel that 
    first acquired each user, not per-session). Returns Total Users, New Users, 
    and Returning Users (computed as Total - New). Matches the GA4 UI's 
    'User acquisition' report.
    """
    property_id = property_id or DEFAULT_GA4_PROPERTY_ID
    if not property_id:
        return "Error: No property_id provided and no DEFAULT_GA4_PROPERTY_ID set."
        
    client = get_ga4_client()
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="firstUserDefaultChannelGroup")],
        metrics=[
            Metric(name="totalUsers"), 
            Metric(name="newUsers"),
            Metric(name="userEngagementDuration"),
            Metric(name="engagedSessions"),
            Metric(name="eventCount"),
            Metric(name="keyEvents"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=limit,
        order_bys=[{"metric": {"metric_name": "totalUsers"}, "desc": True}]
    )
    
    try:
        response = client.run_report(request)
        result = f"User Acquisition for Property {property_id} ({start_date} to {end_date}):\n\n"
        headers = ["Channel", "Total Users", "New Users", "Returning Users", "Avg Engagement Time/User", "Engaged Sessions/User", "Event Count", "Key Events"]
        result += " | ".join(headers) + "\n"
        result += "-" * 100 + "\n"
        
        sum_total_users = 0
        sum_new_users = 0
        sum_returning_users = 0
        sum_engagement_duration = 0.0
        sum_engaged_sessions = 0.0
        sum_event_count = 0
        sum_key_events = 0
        
        for row in response.rows:
            channel = row.dimension_values[0].value
            total_users = int(row.metric_values[0].value)
            new_users = int(row.metric_values[1].value)
            returning_users = total_users - new_users
            engagement_duration = float(row.metric_values[2].value)
            engaged_sessions = float(row.metric_values[3].value)
            event_count = int(row.metric_values[4].value)
            key_events = int(row.metric_values[5].value)
            
            avg_engagement = engagement_duration / total_users if total_users > 0 else 0.0
            engaged_sessions_per_user = engaged_sessions / total_users if total_users > 0 else 0.0
            
            result += f"{channel} | {total_users} | {new_users} | {returning_users} | {avg_engagement:.2f}s | {engaged_sessions_per_user:.2f} | {event_count} | {key_events}\n"
            
            sum_total_users += total_users
            sum_new_users += new_users
            sum_returning_users += returning_users
            sum_engagement_duration += engagement_duration
            sum_engaged_sessions += engaged_sessions
            sum_event_count += event_count
            sum_key_events += key_events
            
        result += "-" * 100 + "\n"
        total_avg_engagement = sum_engagement_duration / sum_total_users if sum_total_users > 0 else 0.0
        total_engaged_sessions_per_user = sum_engaged_sessions / sum_total_users if sum_total_users > 0 else 0.0
        result += f"TOTAL | {sum_total_users} | {sum_new_users} | {sum_returning_users} | {total_avg_engagement:.2f}s | {total_engaged_sessions_per_user:.2f} | {sum_event_count} | {sum_key_events}\n"
        return result
    except Exception as e:
        return f"Error querying GA4: {str(e)}"

@mcp.tool()
def ga4_source_medium(property_id: str = None, start_date: str = "28daysAgo", 
                       end_date: str = "today", limit: int = 10) -> str:
    """
    Get user acquisition data by FIRST USER source / medium. Returns Total Users, 
    New Users, and Returning Users (computed as Total - New) per source/medium.
    """
    property_id = property_id or DEFAULT_GA4_PROPERTY_ID
    if not property_id:
        return "Error: No property_id provided and no DEFAULT_GA4_PROPERTY_ID set."
        
    client = get_ga4_client()
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="firstUserSourceMedium")],
        metrics=[
            Metric(name="totalUsers"), 
            Metric(name="newUsers"),
            Metric(name="userEngagementDuration"),
            Metric(name="engagedSessions"),
            Metric(name="eventCount"),
            Metric(name="keyEvents"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=limit,
        order_bys=[{"metric": {"metric_name": "totalUsers"}, "desc": True}]
    )
    
    try:
        response = client.run_report(request)
        result = f"Source / Medium Breakdown for Property {property_id} ({start_date} to {end_date}):\n\n"
        headers = ["Source/Medium", "Total Users", "New Users", "Returning Users", "Avg Engagement Time/User", "Engaged Sessions/User", "Event Count", "Key Events"]
        result += " | ".join(headers) + "\n"
        result += "-" * 100 + "\n"
        
        sum_total_users = 0
        sum_new_users = 0
        sum_returning_users = 0
        sum_engagement_duration = 0.0
        sum_engaged_sessions = 0.0
        sum_event_count = 0
        sum_key_events = 0
        
        for row in response.rows:
            source_medium = row.dimension_values[0].value
            total_users = int(row.metric_values[0].value)
            new_users = int(row.metric_values[1].value)
            returning_users = total_users - new_users
            engagement_duration = float(row.metric_values[2].value)
            engaged_sessions = float(row.metric_values[3].value)
            event_count = int(row.metric_values[4].value)
            key_events = int(row.metric_values[5].value)
            
            avg_engagement = engagement_duration / total_users if total_users > 0 else 0.0
            engaged_sessions_per_user = engaged_sessions / total_users if total_users > 0 else 0.0
            
            result += f"{source_medium} | {total_users} | {new_users} | {returning_users} | {avg_engagement:.2f}s | {engaged_sessions_per_user:.2f} | {event_count} | {key_events}\n"
            
            sum_total_users += total_users
            sum_new_users += new_users
            sum_returning_users += returning_users
            sum_engagement_duration += engagement_duration
            sum_engaged_sessions += engaged_sessions
            sum_event_count += event_count
            sum_key_events += key_events
            
        result += "-" * 100 + "\n"
        total_avg_engagement = sum_engagement_duration / sum_total_users if sum_total_users > 0 else 0.0
        total_engaged_sessions_per_user = sum_engaged_sessions / sum_total_users if sum_total_users > 0 else 0.0
        result += f"TOTAL | {sum_total_users} | {sum_new_users} | {sum_returning_users} | {total_avg_engagement:.2f}s | {total_engaged_sessions_per_user:.2f} | {sum_event_count} | {sum_key_events}\n"
        return result
    except Exception as e:
        return f"Error querying GA4: {str(e)}"

@mcp.tool()
def ga4_pages_and_screens(property_id: str = None, start_date: str = "28daysAgo",
                           end_date: str = "today", limit: int = 10) -> str:
    """
    Get per-page performance: Views, Active Users, Views per Active User, 
    Avg Engagement Time per Active User, Event Count, Key Events, Total Revenue.
    Matches the GA4 UI's 'Pages and screens' report.
    """
    property_id = property_id or DEFAULT_GA4_PROPERTY_ID
    if not property_id:
        return "Error: No property_id provided and no DEFAULT_GA4_PROPERTY_ID set."
        
    client = get_ga4_client()
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="pagePath")],
        metrics=[
            Metric(name="screenPageViews"),
            Metric(name="activeUsers"),
            Metric(name="userEngagementDuration"),
            Metric(name="eventCount"),
            Metric(name="keyEvents"),
            Metric(name="totalRevenue"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=limit,
        order_bys=[{"metric": {"metric_name": "screenPageViews"}, "desc": True}]
    )
    
    try:
        response = client.run_report(request)
        result = f"Pages and Screens performance for Property {property_id} ({start_date} to {end_date}):\n\n"
        headers = ["Page Path", "Views", "Active Users", "Views per Active User", "Avg Engagement Time per Active User", "Event Count", "Key Events", "Total Revenue"]
        result += " | ".join(headers) + "\n"
        result += "-" * 120 + "\n"
        
        sum_views = 0
        sum_active_users = 0
        sum_engagement_duration = 0.0
        sum_event_count = 0
        sum_key_events = 0
        sum_revenue = 0.0
        
        for row in response.rows:
            page_path = row.dimension_values[0].value
            views = int(row.metric_values[0].value)
            active_users = int(row.metric_values[1].value)
            engagement_duration = float(row.metric_values[2].value)
            event_count = int(row.metric_values[3].value)
            key_events = int(row.metric_values[4].value)
            revenue = float(row.metric_values[5].value)
            
            views_per_active_user = views / active_users if active_users > 0 else 0.0
            avg_engagement = engagement_duration / active_users if active_users > 0 else 0.0
            
            result += f"{page_path} | {views} | {active_users} | {views_per_active_user:.2f} | {avg_engagement:.2f}s | {event_count} | {key_events} | ${revenue:.2f}\n"
            
            sum_views += views
            sum_active_users += active_users
            sum_engagement_duration += engagement_duration
            sum_event_count += event_count
            sum_key_events += key_events
            sum_revenue += revenue
            
        result += "-" * 120 + "\n"
        total_views_per_active_user = sum_views / sum_active_users if sum_active_users > 0 else 0.0
        total_avg_engagement = sum_engagement_duration / sum_active_users if sum_active_users > 0 else 0.0
        
        result += f"TOTAL | {sum_views} | {sum_active_users} | {total_views_per_active_user:.2f} | {total_avg_engagement:.2f}s | {sum_event_count} | {sum_key_events} | ${sum_revenue:.2f}\n"
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
def gtm_list_tags(account_id: str = None, container_id: str = None, workspace_id: str = None) -> str:
    """
    List all active tracking tags in a GTM container workspace.
    Useful for Marketing to audit what is being tracked (e.g. Meta Pixel, LinkedIn Insight).
    """
    account_id = account_id or DEFAULT_GTM_ACCOUNT_ID
    if not account_id:
        return "Error: No account_id provided and no DEFAULT_GTM_ACCOUNT_ID set."
    container_id = container_id or DEFAULT_GTM_CONTAINER_ID
    if not container_id:
        return "Error: No container_id provided and no DEFAULT_GTM_CONTAINER_ID set."
    workspace_id = workspace_id or DEFAULT_GTM_WORKSPACE_ID
    if not workspace_id:
        return "Error: No workspace_id provided and no DEFAULT_GTM_WORKSPACE_ID set."
        
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

@mcp.tool()
def gtm_list_workspaces(account_id: str = None, container_id: str = None) -> str:
    """
    List all workspaces in a GTM container, so their IDs can be used in gtm_list_tags.
    """
    account_id = account_id or DEFAULT_GTM_ACCOUNT_ID
    if not account_id:
        return "Error: No account_id provided and no DEFAULT_GTM_ACCOUNT_ID set."
    container_id = container_id or DEFAULT_GTM_CONTAINER_ID
    if not container_id:
        return "Error: No container_id provided and no DEFAULT_GTM_CONTAINER_ID set."
        
    try:
        service = get_gtm_service()
        path = f"accounts/{account_id}/containers/{container_id}"
        resp = service.accounts().containers().workspaces().list(parent=path).execute()
        workspaces = resp.get('workspace', [])
        
        if not workspaces:
            return f"No workspaces found for container {container_id}."
            
        result = "Workspace Name | ID\n"
        result += "-" * 30 + "\n"
        for ws in workspaces:
            result += f"{ws.get('name')} | {ws.get('workspaceId')}\n"
        return result
    except Exception as e:
        return f"Error listing GTM workspaces: {str(e)}"

if __name__ == "__main__":
    from starlette.responses import PlainTextResponse

    # Register root health-check route
    @mcp.custom_route("/", methods=["GET", "HEAD"])
    async def root_health(request):
        return PlainTextResponse("Marketing MCP Server is running!")

    port = int(os.getenv("PORT", "9000"))
    mcp.settings.port = port
    mcp.settings.host = "0.0.0.0"
    mcp.settings.streamable_http_path = "/mcp"

    print(f"Starting Marketing MCP server on 0.0.0.0:{port} with Streamable HTTP at /mcp")
    mcp.run(transport="streamable-http")
