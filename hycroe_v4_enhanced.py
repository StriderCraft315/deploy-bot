import discord
from discord.ext import commands, tasks
import asyncio
import subprocess
import json
import os
from datetime import datetime, timedelta
import shlex
import logging
import shutil
from typing import Optional, List, Dict, Any
import threading
import time
from dotenv import load_dotenv
import psutil

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('vps_bot')

# Check if lxc command is available
if not shutil.which("lxc"):
    logger.error("LXC command not found. Please ensure LXC is installed.")
    raise SystemExit("LXC command not found. Please ensure LXC is installed.")

# Bot configuration from .env
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', '')
MAIN_ADMIN_ID = int(os.getenv('MAIN_ADMIN_ID', '1133055533318946837'))
BOT_PREFIX = os.getenv('BOT_PREFIX', '.')
BOT_NAME = os.getenv('BOT_NAME', 'XeloraCloud v4.1.0')
BOT_VERSION = os.getenv('BOT_VERSION', '4.1.0')

# System configuration
CPU_THRESHOLD = int(os.getenv('DEFAULT_CPU_THRESHOLD', '90'))
CHECK_INTERVAL = int(os.getenv('DEFAULT_CHECK_INTERVAL', '60'))
PURGE_PROTECTION = os.getenv('PURGE_PROTECTION', 'true').lower() == 'true'
MAINTENANCE_MODE = os.getenv('MAINTENANCE_MODE', 'false').lower() == 'true'
AUTO_STATUS_UPDATE = os.getenv('AUTO_STATUS_UPDATE', 'true').lower() == 'true'
STATUS_UPDATE_INTERVAL = int(os.getenv('STATUS_UPDATE_INTERVAL', '300'))

# Free plans configuration
FREE_PLAN_ENABLED = os.getenv('FREE_PLAN_ENABLED', 'true').lower() == 'true'
INVITE_BOOST_CREDITS = int(os.getenv('INVITE_BOOST_CREDITS', '50'))
MAX_FREE_VPS_PER_USER = int(os.getenv('MAX_FREE_VPS_PER_USER', '1'))

# Bot setup
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents, help_command=None)

# Global variables
VPS_USER_ROLE_ID = None
cpu_monitor_active = True
system_stats = {
    'uptime': datetime.now(),
    'total_vps_created': 0,
    'active_users': 0,
    'commands_executed': 0
}

# Data storage functions
def load_data():
    try:
        with open(os.getenv('USER_DATA_FILE', 'user_data.json'), 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("user_data.json not found or corrupted, initializing empty data")
        return {}

def load_vps_data():
    try:
        with open(os.getenv('VPS_DATA_FILE', 'vps_data.json'), 'r') as f:
            loaded = json.load(f)
            vps_data = {}
            for uid, v in loaded.items():
                if isinstance(v, dict):
                    if "container_name" in v:
                        vps_data[uid] = [v]
                    else:
                        vps_data[uid] = list(v.values())
                elif isinstance(v, list):
                    vps_data[uid] = v
                else:
                    logger.warning(f"Unknown VPS data format for user {uid}, skipping")
                    continue
            return vps_data
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("vps_data.json not found or corrupted, initializing empty data")
        return {}

def load_admin_data():
    try:
        with open(os.getenv('ADMIN_DATA_FILE', 'admin_data.json'), 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("admin_data.json not found or corrupted, initializing with main admin")
        return {"admins": [str(MAIN_ADMIN_ID)], "purge_protection": {"enabled": True, "protected_users": [], "protected_vps": 0}}

# Load all data at startup
user_data = load_data()
vps_data = load_vps_data()
admin_data = load_admin_data()

# Ensure purge protection exists
if 'purge_protection' not in admin_data:
    admin_data['purge_protection'] = {"enabled": True, "protected_users": [], "protected_vps": 0}

def save_data():
    try:
        with open(os.getenv('USER_DATA_FILE', 'user_data.json'), 'w') as f:
            json.dump(user_data, f, indent=4)
        with open(os.getenv('VPS_DATA_FILE', 'vps_data.json'), 'w') as f:
            json.dump(vps_data, f, indent=4)
        with open(os.getenv('ADMIN_DATA_FILE', 'admin_data.json'), 'w') as f:
            json.dump(admin_data, f, indent=4)
        logger.info("Data saved successfully")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

# Admin checks
def is_admin():
    async def predicate(ctx):
        user_id = str(ctx.author.id)
        if user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", []):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "You don't have permission to use this command."))
        return False
    return commands.check(predicate)

def is_main_admin():
    async def predicate(ctx):
        if str(ctx.author.id) == str(MAIN_ADMIN_ID):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "Only the main admin can use this command."))
        return False
    return commands.check(predicate)

def maintenance_check():
    async def predicate(ctx):
        if MAINTENANCE_MODE and str(ctx.author.id) != str(MAIN_ADMIN_ID):
            await ctx.send(embed=create_warning_embed("Maintenance Mode", "The bot is currently under maintenance. Only the main admin can use commands."))
            return False
        return True
    return commands.check(predicate)

# Enhanced embed creation functions
def create_embed(title, description="", color=0x1a1a1a, fields=None, thumbnail=True):
    """Create a dark-themed embed with enhanced styling"""
    embed = discord.Embed(
        title=f"▌ {title}",
        description=description,
        color=color,
        timestamp=datetime.now()
    )

    if thumbnail:
        embed.set_thumbnail(url="https://i.imghippo.com/files/Mf6255KwU.png")

    if fields:
        for field in fields:
            embed.add_field(
                name=f"▸ {field['name']}",
                value=field["value"],
                inline=field.get("inline", False)
            )

    embed.set_footer(
        text=f"{BOT_NAME} • NjanFlame • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        icon_url="https://i.imghippo.com/files/Mf6255KwU.png"
    )

    return embed

def create_success_embed(title, description=""):
    return create_embed(title, description, color=0x00ff88)

def create_error_embed(title, description=""):
    return create_embed(title, description, color=0xff3366)

def create_info_embed(title, description=""):
    return create_embed(title, description, color=0x00ccff)

def create_warning_embed(title, description=""):
    return create_embed(title, description, color=0xffaa00)

def create_premium_embed(title, description=""):
    return create_embed(title, description, color=0xffd700)

# Enhanced LXC execution
async def execute_lxc(command, timeout=120):
    """Execute LXC command with timeout and error handling"""
    try:
        cmd = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        if proc.returncode != 0:
            error = stderr.decode().strip() if stderr else "Command failed with no error output"
            raise Exception(error)

        return stdout.decode().strip() if stdout else True
    except asyncio.TimeoutError:
        logger.error(f"LXC command timed out: {command}")
        raise Exception(f"Command timed out after {timeout} seconds")
    except Exception as e:
        logger.error(f"LXC Error: {command} - {str(e)}")
        raise

# System monitoring functions
def get_system_info():
    """Get comprehensive system information"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            'cpu_usage': cpu_percent,
            'memory_usage': memory.percent,
            'memory_total': memory.total // (1024**3),  # GB
            'memory_available': memory.available // (1024**3),  # GB
            'disk_usage': disk.percent,
            'disk_total': disk.total // (1024**3),  # GB
            'disk_free': disk.free // (1024**3),  # GB
        }
    except Exception as e:
        logger.error(f"Error getting system info: {e}")
        return None

async def get_vps_status(container_name):
    """Get real-time VPS status"""
    try:
        result = await execute_lxc(f"lxc info {container_name}")
        if "Status: Running" in result:
            return "running"
        elif "Status: Stopped" in result:
            return "stopped"
        else:
            return "unknown"
    except:
        return "error"

# VPS role management
async def get_or_create_vps_role(guild):
    """Get or create the VPS User role"""
    global VPS_USER_ROLE_ID
    
    if VPS_USER_ROLE_ID:
        role = guild.get_role(VPS_USER_ROLE_ID)
        if role:
            return role
    
    role = discord.utils.get(guild.roles, name="VPS User")
    if role:
        VPS_USER_ROLE_ID = role.id
        return role
    
    try:
        role = await guild.create_role(
            name="VPS User",
            color=discord.Color.dark_purple(),
            reason="VPS User role for bot management",
            permissions=discord.Permissions.none()
        )
        VPS_USER_ROLE_ID = role.id
        logger.info(f"Created VPS User role: {role.name} (ID: {role.id})")
        return role
    except Exception as e:
        logger.error(f"Failed to create VPS User role: {e}")
        return None

# Auto status update system
@tasks.loop(seconds=STATUS_UPDATE_INTERVAL)
async def auto_status_update():
    """Automatically update VPS statuses"""
    if not AUTO_STATUS_UPDATE:
        return
    
    try:
        logger.info("Starting auto status update...")
        updated_count = 0
        
        for user_id, vps_list in vps_data.items():
            for vps in vps_list:
                container_name = vps.get('container_name')
                if container_name:
                    current_status = await get_vps_status(container_name)
                    if current_status != vps.get('status') and current_status != 'error':
                        vps['status'] = current_status
                        vps['last_updated'] = datetime.now().isoformat()
                        updated_count += 1
        
        if updated_count > 0:
            save_data()
            logger.info(f"Auto status update completed: {updated_count} VPS updated")
    except Exception as e:
        logger.error(f"Error in auto status update: {e}")

# CPU monitoring
def cpu_monitor():
    """Monitor CPU usage and stop all VPS if threshold is exceeded"""
    global cpu_monitor_active
    
    while cpu_monitor_active:
        try:
            system_info = get_system_info()
            if system_info and system_info['cpu_usage'] > CPU_THRESHOLD:
                logger.warning(f"CPU usage ({system_info['cpu_usage']}%) exceeded threshold ({CPU_THRESHOLD}%). Stopping all VPS.")
                
                try:
                    subprocess.run(['lxc', 'stop', '--all', '--force'], check=True)
                    logger.info("All VPS stopped due to high CPU usage")
                    
                    # Update all VPS status in database
                    for user_id, vps_list in vps_data.items():
                        for vps in vps_list:
                            if vps.get('status') == 'running':
                                vps['status'] = 'stopped'
                                vps['last_updated'] = datetime.now().isoformat()
                    save_data()
                except Exception as e:
                    logger.error(f"Error stopping all VPS: {e}")
            
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Error in CPU monitor: {e}")
            time.sleep(CHECK_INTERVAL)

# Start monitoring systems
cpu_thread = threading.Thread(target=cpu_monitor, daemon=True)
cpu_thread.start()

# Enhanced Help System with Pagination
class HelpView(discord.ui.View):
    def __init__(self, user_id, is_admin=False):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.is_admin = is_admin
        self.current_page = 1
        self.max_pages = 3
        
        self.update_buttons()
    
    def update_buttons(self):
        self.clear_items()
        
        # Navigation buttons
        first_btn = discord.ui.Button(emoji="⏪", label="First", disabled=self.current_page == 1)
        first_btn.callback = self.first_page
        self.add_item(first_btn)
        
        prev_btn = discord.ui.Button(emoji="◀️", label="Previous", disabled=self.current_page == 1)
        prev_btn.callback = self.prev_page
        self.add_item(prev_btn)
        
        page_btn = discord.ui.Button(label=f"Page {self.current_page}/3", style=discord.ButtonStyle.secondary, disabled=True)
        self.add_item(page_btn)
        
        next_btn = discord.ui.Button(emoji="▶️", label="Next", disabled=self.current_page == self.max_pages)
        next_btn.callback = self.next_page
        self.add_item(next_btn)
        
        last_btn = discord.ui.Button(emoji="⏩", label="Last", disabled=self.current_page == self.max_pages)
        last_btn.callback = self.last_page
        self.add_item(last_btn)
        
        # Close button
        close_btn = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger)
        close_btn.callback = self.close_help
        self.add_item(close_btn)
    
    def get_page_embed(self):
        if self.current_page == 1:
            return self.get_user_commands_embed()
        elif self.current_page == 2:
            return self.get_admin_commands_embed() if self.is_admin else self.get_user_commands_embed()
        else:
            return self.get_system_info_embed()
    
    def get_user_commands_embed(self):
        embed = create_embed("🔧 XeloraCloud VPS Management - Help (Page 1/3)", f"{BOT_NAME}\nUser Commands", 0x1a1a1a)
        
        commands_list = [
            "`.plans`\nView available VPS plans",
            "`.manage [user]`\nManage your VPS (admin: others')",
            "`.buywc <plan>`\nBuy VPS with credits",
            "`.freeplans`\nView free plans (boost/invite)",
            "`.credits`\nCheck your credits balance",
            "`.shareuser <user> <vps#>`\nShare VPS access",
            "`.shareruser <user> <vps#>`\nRevoke shared access",
            "`.manageshared <owner> <vps#>`\nManage shared VPS"
        ]
        
        embed.add_field(name="User Commands", value="\n\n".join(commands_list), inline=False)
        return embed
    
    def get_admin_commands_embed(self):
        if not self.is_admin:
            return self.get_user_commands_embed()
            
        embed = create_embed("⚙️ XeloraCloud VPS Management - Help (Page 2/3)", f"{BOT_NAME}\nAdmin Commands", 0x1a1a1a)
        
        commands_list = [
            "`.deploy <user>`\nDeploy VPS for user",
            "`.editplans`\nEdit VPS plans",
            "`.addpaid <name> <price> <ram> <cpu> <storage> <desc>`\nAdd paid plan",
            "`.addboost <name> <boosts> <ram> <cpu> <storage>`\nAdd boost plan",
            "`.addinvite <name> <invites> <ram> <cpu> <storage>`\nAdd invite plan",
            "`.removeplan <type> <name>`\nRemove plan",
            "`.create <user> <ram> <cpu> <storage>`\nCreate custom VPS",
            "`.listall`\nList all VPS and users",
            "`.suspendvps <user>`\nSuspend a VPS (interactive)",
            "`.stopall [reason]`\nStop all VPS",
            "`.unsuspend <user> <vps#>`\nUnsuspend a VPS",
            "`.upgradevps <user> <vps#> <ram> <cpu>`\nUpgrade VPS",
            "`.deletevps <user>`\nDelete user's VPS (interactive)",
            "`.userinfo <user>`\nGet user information",
            "`.vpsinfo <vps_id>`\nGet VPS information",
            "`.adminc <user> <amount>`\nAdd credits",
            "`.adminrc <user> <amount/all>`\nRemove credits",
            "`.plansedit <type> <plan> <description>`\nEdit plan description"
        ]
        
        embed.add_field(name="Admin Commands", value="\n\n".join(commands_list), inline=False)
        return embed
    
    def get_system_info_embed(self):
        embed = create_embed("📊 XeloraCloud VPS Management - Help (Page 3/3)", f"{BOT_NAME}\nPurge System & Information", 0x1a1a1a)
        
        # System information
        system_info = get_system_info()
        total_vps = sum(len(vps_list) for vps_list in vps_data.values())
        running_vps = sum(1 for vps_list in vps_data.values() for vps in vps_list if vps.get('status') == 'running')
        
        system_text = f"**Developer:** XeloraCloud\n**Processor:** Ryzen 9 7900\n**Network:** IPv6 Only (no IPv4)\n**Ticket Required:** ✅ Yes"
        
        purge_status = "✅ ACTIVE" if admin_data.get('purge_protection', {}).get('enabled') else "❌ INACTIVE"
        protected_users = len(admin_data.get('purge_protection', {}).get('protected_users', []))
        
        embed.add_field(name="⚡ System Information", value=system_text, inline=False)
        
        if system_info:
            stats_text = f"**Status:** ✅ Normal\n**Protected Users:** {protected_users}\n**Protected VPS:** {admin_data.get('purge_protection', {}).get('protected_vps', 0)}\n**Interval:** Every 5 minutes"
            embed.add_field(name="⚠️ Purge System", value=f"**Status:** {purge_status}\n{stats_text}", inline=False)
        
        embed.add_field(name="🛡️ Protection Info", value="Protect your VPS with `.dontpurgevps @you`!", inline=False)
        
        if self.is_admin:
            maintenance_status = "✅ Normal" if not MAINTENANCE_MODE else "⚠️ Maintenance"
            embed.add_field(name="⚙️ Maintenance Mode", value=f"**Status:** {maintenance_status}\n**Commands Active:** All Users\n**Toggle:** `.maintenance on/off` (Main Admin only)", inline=False)
        
        return embed
    
    async def first_page(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This help menu is not for you!", ephemeral=True)
            return
        
        self.current_page = 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    async def prev_page(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This help menu is not for you!", ephemeral=True)
            return
        
        if self.current_page > 1:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    async def next_page(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This help menu is not for you!", ephemeral=True)
            return
        
        if self.current_page < self.max_pages:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    async def last_page(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This help menu is not for you!", ephemeral=True)
            return
        
        self.current_page = self.max_pages
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    async def close_help(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This help menu is not for you!", ephemeral=True)
            return
        
        await interaction.response.edit_message(embed=create_info_embed("Help Closed", "Help menu has been closed."), view=None)

# Bot events
@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching, 
            name=f"XeloraCloud VPS Manager"
        )
    )
    
    # Start auto status update
    if AUTO_STATUS_UPDATE:
        auto_status_update.start()
        logger.info("Auto status update system started")
    
    logger.info("Bot is ready with enhanced features!")

@bot.event
async def on_command_error(ctx, error):
    system_stats['commands_executed'] += 1
    
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=create_error_embed("Missing Argument", "Please use `.help` for command usage."))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=create_error_embed("Invalid Argument", "Please check your input and try again."))
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(embed=create_error_embed("System Error", "An error occurred. Please try again."))

# Enhanced Help Command
@bot.command(name='help', aliases=['h'])
@maintenance_check()
async def help_command(ctx):
    """Enhanced help system with pagination"""
    user_id = str(ctx.author.id)
    is_admin = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", [])
    
    view = HelpView(user_id, is_admin)
    await ctx.send(embed=view.get_page_embed(), view=view)

# Credits command
@bot.command(name='credits')
@maintenance_check()
async def check_credits(ctx):
    """Check your credits balance"""
    user_id = str(ctx.author.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    
    balance = user_data[user_id]["credits"]
    
    embed = create_embed("💰 Credits Balance", f"Your current credits balance", 0x1a1a1a)
    embed.add_field(name="Balance", value=f"**{balance:,}** credits", inline=False)
    embed.add_field(name="Quick Actions", value="• `.buyc` - Purchase credits\n• `.plans` - View VPS plans\n• `.buywc <plan>` - Buy VPS", inline=False)
    
    await ctx.send(embed=embed)

# Enhanced Interactive Deploy System
class DeploymentView(discord.ui.View):
    def __init__(self, admin_id):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.selected_plan_type = None
        self.selected_plan = None
        self.selected_user = None
        self.selected_os = None
        
        # Add initial plan type selector
        self.add_plan_type_selector()
    
    def add_plan_type_selector(self):
        """Add plan type selector dropdown"""
        self.clear_items()
        
        plan_type_options = [
            discord.SelectOption(
                label="💎 Paid Plans",
                description="Credit-based VPS plans with premium features",
                value="paid",
                emoji="💎"
            ),
            discord.SelectOption(
                label="🆓 Free Plans",
                description="Boost and invite-based free VPS plans",
                value="free",
                emoji="🆓"
            ),
            discord.SelectOption(
                label="⚙️ Custom VPS",
                description="Create custom VPS with specific resources",
                value="custom",
                emoji="⚙️"
            )
        ]
        
        plan_type_select = discord.ui.Select(
            placeholder="🎯 Select deployment type...",
            options=plan_type_options
        )
        plan_type_select.callback = self.plan_type_selected
        self.add_item(plan_type_select)
    
    async def plan_type_selected(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.admin_id:
            await interaction.response.send_message("This deployment panel is not for you!", ephemeral=True)
            return
        
        self.selected_plan_type = interaction.data['values'][0]
        
        if self.selected_plan_type == "paid":
            await self.show_paid_plans(interaction)
        elif self.selected_plan_type == "free":
            await self.show_free_plans(interaction)
        else:  # custom
            await self.show_custom_options(interaction)
    
    async def show_paid_plans(self, interaction):
        """Show paid plan options"""
        self.clear_items()
        
        paid_plans = [
            {"name": "Starter", "price": 42, "ram": "4GB", "cpu": "1", "storage": "10GB"},
            {"name": "Basic", "price": 96, "ram": "8GB", "cpu": "1", "storage": "10GB"},
            {"name": "Standard", "price": 192, "ram": "12GB", "cpu": "2", "storage": "10GB"},
            {"name": "Pro", "price": 220, "ram": "16GB", "cpu": "2", "storage": "10GB"}
        ]
        
        plan_options = []
        for plan in paid_plans:
            plan_options.append(discord.SelectOption(
                label=f"{plan['name']} Plan",
                description=f"{plan['ram']} RAM • {plan['cpu']} CPU • {plan['storage']} Storage • {plan['price']} credits",
                value=plan['name']
            ))
        
        plan_select = discord.ui.Select(
            placeholder="💎 Select paid plan...",
            options=plan_options
        )
        plan_select.callback = self.paid_plan_selected
        self.add_item(plan_select)
        
        # Add back button
        back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
        back_btn.callback = self.go_back
        self.add_item(back_btn)
        
        embed = create_embed("💎 Paid Plans", "Select a paid plan to deploy:", 0x1a1a1a)
        embed.add_field(name="💡 Info", value="These plans require credits. User must have sufficient balance.", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def show_free_plans(self, interaction):
        """Show free plan options"""
        self.clear_items()
        
        free_plans = [
            {"name": "Boost Starter", "type": "boost", "req": "1 boost", "ram": "2GB", "cpu": "1", "storage": "5GB"},
            {"name": "Boost Basic", "type": "boost", "req": "2 boosts", "ram": "4GB", "cpu": "1", "storage": "10GB"},
            {"name": "Invite Starter", "type": "invite", "req": "5 invites", "ram": "1GB", "cpu": "1", "storage": "5GB"},
            {"name": "Invite Basic", "type": "invite", "req": "10 invites", "ram": "2GB", "cpu": "1", "storage": "8GB"}
        ]
        
        plan_options = []
        for plan in free_plans:
            emoji = "🚀" if plan['type'] == 'boost' else "👥"
            plan_options.append(discord.SelectOption(
                label=f"{plan['name']}",
                description=f"{plan['ram']} RAM • {plan['cpu']} CPU • {plan['storage']} Storage • {plan['req']}",
                value=plan['name'],
                emoji=emoji
            ))
        
        plan_select = discord.ui.Select(
            placeholder="🆓 Select free plan...",
            options=plan_options
        )
        plan_select.callback = self.free_plan_selected
        self.add_item(plan_select)
        
        # Add back button
        back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
        back_btn.callback = self.go_back
        self.add_item(back_btn)
        
        embed = create_embed("🆓 Free Plans", "Select a free plan to deploy:", 0x1a1a1a)
        embed.add_field(name="💡 Info", value="Free plans are manually verified by admins based on boosts/invites.", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def show_custom_options(self, interaction):
        """Show custom VPS creation form"""
        embed = create_embed("⚙️ Custom VPS Deployment", "Use the command below for custom VPS:", 0x1a1a1a)
        embed.add_field(name="📋 Command Format", 
            value="```\n.create @user <ram_gb> <cpu_cores> <disk_gb>\n```", inline=False)
        embed.add_field(name="💡 Example", 
            value="```\n.create @JohnDoe 8 2 20\n```\n(8GB RAM, 2 CPU cores, 20GB disk)", inline=False)
        embed.add_field(name="⚙️ Supported Ranges", 
            value="**RAM:** 1-32 GB\n**CPU:** 1-8 cores\n**Disk:** 5-100 GB", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=None)
    
    async def paid_plan_selected(self, interaction):
        """Handle paid plan selection"""
        if str(interaction.user.id) != self.admin_id:
            await interaction.response.send_message("This deployment panel is not for you!", ephemeral=True)
            return
        
        self.selected_plan = interaction.data['values'][0]
        await self.show_user_selector(interaction)
    
    async def free_plan_selected(self, interaction):
        """Handle free plan selection"""
        if str(interaction.user.id) != self.admin_id:
            await interaction.response.send_message("This deployment panel is not for you!", ephemeral=True)
            return
        
        self.selected_plan = interaction.data['values'][0]
        await self.show_user_selector(interaction)
    
    async def show_user_selector(self, interaction):
        """Show user selection interface"""
        self.clear_items()
        
        # Create user input modal
        class UserSelectionModal(discord.ui.Modal):
            def __init__(self, parent_view):
                super().__init__(title="Select User for VPS Deployment")
                self.parent_view = parent_view
                
                self.user_input = discord.ui.TextInput(
                    label="User ID or Mention",
                    placeholder="Enter user ID (123456789) or mention (@username)",
                    required=True,
                    max_length=100
                )
                self.add_item(self.user_input)
            
            async def on_submit(self, interaction: discord.Interaction):
                user_input = self.user_input.value.strip()
                
                # Try to get user
                target_user = None
                try:
                    # If it's a mention format
                    if user_input.startswith('<@') and user_input.endswith('>'):
                        user_id = int(user_input.strip('<@!>'))
                        target_user = interaction.guild.get_member(user_id) or await interaction.client.fetch_user(user_id)
                    # If it's just a user ID
                    elif user_input.isdigit():
                        target_user = interaction.guild.get_member(int(user_input)) or await interaction.client.fetch_user(int(user_input))
                    # If it's a username
                    else:
                        target_user = discord.utils.get(interaction.guild.members, name=user_input)
                except:
                    pass
                
                if not target_user:
                    await interaction.response.send_message("❌ User not found! Please use a valid user ID, mention, or username.", ephemeral=True)
                    return
                
                self.parent_view.selected_user = target_user
                await self.parent_view.show_os_selector(interaction)
        
        # Add user selection button
        user_btn = discord.ui.Button(label="👤 Select User", style=discord.ButtonStyle.primary)
        user_btn.callback = lambda i: i.response.send_modal(UserSelectionModal(self))
        self.add_item(user_btn)
        
        # Add back button
        back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
        back_btn.callback = self.go_back_to_plans
        self.add_item(back_btn)
        
        embed = create_embed("👤 User Selection", f"Selected Plan: **{self.selected_plan}**", 0x1a1a1a)
        embed.add_field(name="📋 Next Step", value="Click the button below to select the user who will receive the VPS.", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def show_os_selector(self, interaction):
        """Show OS selection"""
        self.clear_items()
        
        os_options = [
            discord.SelectOption(
                label="Ubuntu 22.04 LTS",
                description="Latest Ubuntu LTS with full package support",
                value="ubuntu:22.04",
                emoji="🐧"
            ),
            discord.SelectOption(
                label="Ubuntu 24.04 LTS", 
                description="Newest Ubuntu LTS release",
                value="ubuntu:24.04",
                emoji="🐧"
            ),
            discord.SelectOption(
                label="Debian 11",
                description="Stable Debian Bullseye release",
                value="images:debian/11",
                emoji="🌀"
            ),
            discord.SelectOption(
                label="CentOS 8",
                description="Enterprise-focused CentOS distribution",
                value="images:centos/8",
                emoji="🎯"
            )
        ]
        
        os_select = discord.ui.Select(
            placeholder="💿 Select operating system...",
            options=os_options
        )
        os_select.callback = self.os_selected
        self.add_item(os_select)
        
        # Add back button
        back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
        back_btn.callback = lambda i: self.show_user_selector(i)
        self.add_item(back_btn)
        
        embed = create_embed("💿 OS Selection", f"Deploying **{self.selected_plan}** for **{self.selected_user.display_name}**", 0x1a1a1a)
        embed.add_field(name="📋 Choose OS", value="Select the operating system for the VPS deployment.", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def os_selected(self, interaction):
        """Handle OS selection and deploy VPS"""
        if str(interaction.user.id) != self.admin_id:
            await interaction.response.send_message("This deployment panel is not for you!", ephemeral=True)
            return
        
        self.selected_os = interaction.data['values'][0]
        await self.deploy_vps(interaction)
    
    async def deploy_vps(self, interaction):
        """Deploy the VPS with selected options"""
        await interaction.response.defer()
        
        try:
            # Get plan specifications
            plan_specs = self.get_plan_specs(self.selected_plan)
            if not plan_specs:
                await interaction.followup.send("❌ Invalid plan selected!", ephemeral=True)
                return
            
            # Check user eligibility for free plans
            if self.selected_plan_type == "free":
                # This would normally check boost/invite requirements
                # For now, we'll just deploy as admin authorized
                pass
            
            user_id = str(self.selected_user.id)
            if user_id not in vps_data:
                vps_data[user_id] = []
            
            vps_count = len(vps_data[user_id]) + 1
            username = self.selected_user.name.replace(" ", "_").lower()
            container_name = f"vps-{username}-{vps_count}"
            
            # Create deployment embed
            deploy_embed = create_info_embed("🚀 Deploying VPS", f"Creating {self.selected_plan} VPS for {self.selected_user.mention}...")
            deploy_embed.add_field(name="📊 Specifications", 
                value=f"**Plan:** {self.selected_plan}\n**OS:** {self.selected_os}\n**RAM:** {plan_specs['ram']}\n**CPU:** {plan_specs['cpu']} cores\n**Storage:** {plan_specs['storage']}\n**Container:** `{container_name}`", 
                inline=False)
            deploy_embed.add_field(name="🔄 Status", value="⏳ **Launching container...**", inline=False)
            
            await interaction.followup.send(embed=deploy_embed)
            
            # Deploy VPS
            ram_mb = int(plan_specs['ram'].replace('GB', '')) * 1024
            
            deploy_embed.set_field_at(1, name="🔄 Status", value=f"📦 **Installing {self.selected_os}...**", inline=False)
            await interaction.edit_original_response(embed=deploy_embed)
            
            await execute_lxc(f"lxc launch {self.selected_os} {container_name} --config limits.memory={ram_mb}MB --config limits.cpu={plan_specs['cpu']} -s dir")
            
            # Save to database
            vps_info = {
                "container_name": container_name,
                "plan": self.selected_plan,
                "ram": plan_specs['ram'],
                "cpu": plan_specs['cpu'],
                "storage": plan_specs['storage'],
                "os": self.selected_os,
                "status": "running",
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "deployed_by": self.admin_id,
                "plan_type": self.selected_plan_type,
                "shared_with": []
            }
            vps_data[user_id].append(vps_info)
            system_stats['total_vps_created'] += 1
            save_data()
            
            # Success embed
            success_embed = create_success_embed("✅ VPS Deployment Complete!", f"Successfully deployed {self.selected_plan} VPS for {self.selected_user.mention}")
            success_embed.add_field(name="📊 VPS Details", 
                value=f"**Container:** `{container_name}`\n**Plan:** {self.selected_plan}\n**OS:** {self.selected_os}\n**Resources:** {plan_specs['ram']} RAM • {plan_specs['cpu']} CPU • {plan_specs['storage']} Storage", 
                inline=False)
            success_embed.add_field(name="🎯 Next Steps", 
                value=f"• User can access with `.manage`\n• VPS is running and ready to use\n• SSH access available immediately", 
                inline=False)
            
            await interaction.edit_original_response(embed=success_embed)
            
            # Notify user
            try:
                dm_embed = create_success_embed("🎉 VPS Deployed!", f"Your {self.selected_plan} VPS has been deployed by an admin!")
                dm_embed.add_field(name="📊 VPS Information", 
                    value=f"**VPS ID:** #{vps_count}\n**Plan:** {self.selected_plan}\n**Container:** `{container_name}`\n**OS:** {self.selected_os}\n**Resources:** {plan_specs['ram']} RAM • {plan_specs['cpu']} CPU • {plan_specs['storage']} Storage", 
                    inline=False)
                dm_embed.add_field(name="🚀 Get Started", 
                    value="• Type `.manage` to access your VPS\n• Use **SSH Access** button for terminal\n• VPS is ready to use immediately!", 
                    inline=False)
                await self.selected_user.send(embed=dm_embed)
            except discord.Forbidden:
                pass
            
        except Exception as e:
            error_embed = create_error_embed("❌ Deployment Failed", f"Failed to deploy VPS: {str(e)}")
            await interaction.edit_original_response(embed=error_embed)
    
    def get_plan_specs(self, plan_name):
        """Get specifications for a plan"""
        paid_plans = {
            "Starter": {"ram": "4GB", "cpu": "1", "storage": "10GB"},
            "Basic": {"ram": "8GB", "cpu": "1", "storage": "10GB"}, 
            "Standard": {"ram": "12GB", "cpu": "2", "storage": "10GB"},
            "Pro": {"ram": "16GB", "cpu": "2", "storage": "10GB"}
        }
        
        free_plans = {
            "Boost Starter": {"ram": "2GB", "cpu": "1", "storage": "5GB"},
            "Boost Basic": {"ram": "4GB", "cpu": "1", "storage": "10GB"},
            "Invite Starter": {"ram": "1GB", "cpu": "1", "storage": "5GB"},
            "Invite Basic": {"ram": "2GB", "cpu": "1", "storage": "8GB"}
        }
        
        return paid_plans.get(plan_name) or free_plans.get(plan_name)
    
    async def go_back(self, interaction):
        """Go back to plan type selection"""
        if str(interaction.user.id) != self.admin_id:
            await interaction.response.send_message("This deployment panel is not for you!", ephemeral=True)
            return
        
        self.selected_plan_type = None
        self.selected_plan = None
        self.add_plan_type_selector()
        
        embed = create_embed("🚀 VPS Deployment Center", "Choose your deployment method:", 0x1a1a1a)
        embed.add_field(name="💡 Deployment Options", 
            value="• **Paid Plans** - Credit-based VPS with premium features\n• **Free Plans** - Boost/invite-based VPS\n• **Custom VPS** - Manual resource specification", 
            inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def go_back_to_plans(self, interaction):
        """Go back to plan selection"""
        if str(interaction.user.id) != self.admin_id:
            await interaction.response.send_message("This deployment panel is not for you!", ephemeral=True)
            return
        
        if self.selected_plan_type == "paid":
            await self.show_paid_plans(interaction)
        elif self.selected_plan_type == "free":
            await self.show_free_plans(interaction)

@bot.command(name='deploy')
@is_admin()
@maintenance_check()
async def deploy_command(ctx):
    """Interactive VPS deployment center with dropdowns"""
    embed = create_embed("🚀 VPS Deployment Center", "Choose your deployment method:", 0x1a1a1a)
    
    # System overview
    total_users = len(vps_data)
    total_vps = sum(len(vps_list) for vps_list in vps_data.values())
    running_vps = sum(1 for vps_list in vps_data.values() for vps in vps_list if vps.get('status') == 'running')
    
    embed.add_field(name="📊 System Overview", 
        value=f"**Total Users:** {total_users}\n**Total VPS:** {total_vps}\n**Running:** {running_vps}\n**Stopped:** {total_vps - running_vps}", 
        inline=True)
    
    system_info = get_system_info()
    if system_info:
        embed.add_field(name="⚙️ System Resources", 
            value=f"**CPU:** {system_info['cpu_usage']:.1f}%\n**Memory:** {system_info['memory_usage']:.1f}%\n**Disk:** {system_info['disk_usage']:.1f}%\n**Free Space:** {system_info['disk_free']}GB", 
            inline=True)
    
    embed.add_field(name="💡 Deployment Options", 
        value="• **Paid Plans** - Credit-based VPS with premium features\n• **Free Plans** - Boost/invite-based VPS\n• **Custom VPS** - Manual resource specification", 
        inline=False)
    
    view = DeploymentView(str(ctx.author.id))
    await ctx.send(embed=embed, view=view)

# Enhanced VPS Management View with Dashboard
class EnhancedManageView(discord.ui.View):
    def __init__(self, user_id, vps_list, is_shared=False, owner_id=None, is_admin=False):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.vps_list = vps_list
        self.selected_index = None
        self.is_shared = is_shared
        self.owner_id = owner_id or user_id
        self.is_admin = is_admin

        if len(vps_list) > 1:
            options = [
                discord.SelectOption(
                    label=f"VPS {i+1} - {v.get('plan', 'Custom')}",
                    description=f"Status: {v.get('status', 'unknown').title()} | {v.get('ram', 'N/A')} RAM",
                    value=str(i),
                    emoji="🟢" if v.get('status') == 'running' else "🔴"
                ) for i, v in enumerate(vps_list)
            ]
            self.select = discord.ui.Select(placeholder="🖥️ Select a VPS to manage", options=options)
            self.select.callback = self.select_vps
            self.add_item(self.select)
            self.initial_embed = self.create_dashboard_embed()
        else:
            self.selected_index = 0
            self.initial_embed = self.create_detailed_vps_embed(0)
            self.add_action_buttons()

    def create_dashboard_embed(self):
        """Create VPS Management Dashboard"""
        total_vps = len(self.vps_list)
        running_count = sum(1 for vps in self.vps_list if vps.get('status') == 'running')
        stopped_count = total_vps - running_count
        
        owner_text = ""
        if self.is_admin and self.owner_id != self.user_id:
            try:
                owner_user = bot.get_user(int(self.owner_id))
                owner_text = f"\n**Owner:** {owner_user.mention if owner_user else f'ID: {self.owner_id}'}"
            except:
                owner_text = f"\n**Owner ID:** {self.owner_id}"

        embed = create_embed(
            "💻 VPS Management Dashboard",
            f"You have access to **{total_vps}** virtual server{'s' if total_vps != 1 else ''}{owner_text}",
            0x1a1a1a
        )

        # Status overview
        status_text = f"🟢 **{running_count}** Running\n🔴 **{stopped_count}** Stopped"
        embed.add_field(name="📊 Server Status", value=status_text, inline=True)

        # Accessible Servers list
        servers_list = []
        for i, vps in enumerate(self.vps_list):
            status_emoji = "🟢" if vps.get('status') == 'running' else "🔴"
            plan_text = vps.get('plan', 'Custom')
            container = vps.get('container_name', 'Unknown')
            
            servers_list.append(f"{status_emoji} **VPS {i+1}** - `{container}` ({'Owner' if not self.is_shared else 'Shared'})")
            
            # Add resource info
            resources = f"• Plan: {plan_text}\n• Status: {vps.get('status', 'unknown').title()}\n• Resources: {vps.get('ram', 'N/A')} RAM • {vps.get('cpu', 'N/A')} CPU • {vps.get('storage', 'N/A')} Storage"
            servers_list.append(resources)

        if servers_list:
            embed.add_field(name="🖥️ Accessible Servers", value="\n".join(servers_list), inline=False)

        # Quick Actions
        actions = "• Select a server from the dropdown\n• Start/Stop server power\n• Get SSH access\n• Reinstall OS (owner only)"
        embed.add_field(name="⚡ Quick Actions", value=actions, inline=False)

        return embed

    def create_detailed_vps_embed(self, index):
        """Create detailed VPS management embed"""
        vps = self.vps_list[index]
        status = vps.get('status', 'unknown')
        
        # Dynamic colors based on status
        if status == 'running':
            status_color = 0x00ff88  # Green for running/online
            status_emoji = "🟢"
            status_text = "ONLINE"
        elif status == 'stopped':
            status_color = 0xff3366  # Red for stopped/offline  
            status_emoji = "🔴"
            status_text = "OFFLINE"
        elif status == 'suspended':
            status_color = 0xffaa00  # Yellow for suspended
            status_emoji = "⏸️"
            status_text = "SUSPENDED"
        else:
            status_color = 0x666666  # Gray for unknown
            status_emoji = "❓"
            status_text = "UNKNOWN"
        
        # Get real-time status if possible
        container_name = vps.get('container_name', 'Unknown')
        
        owner_text = ""
        if self.is_admin and self.owner_id != self.user_id:
            try:
                owner_user = bot.get_user(int(self.owner_id))
                owner_text = f"\n**Owner:** {owner_user.mention if owner_user else f'ID: {self.owner_id}'}"
            except:
                owner_text = f"\n**Owner ID:** {self.owner_id}"

        embed = create_embed(
            f"VPS Management - {vps.get('plan', 'Custom')}",
            f"Container: `{container_name}`{owner_text}",
            status_color
        )

        # Status section with enhanced info
        created_date = vps.get('created_at', 'Unknown')
        if created_date != 'Unknown':
            try:
                created_dt = datetime.fromisoformat(created_date.replace('Z', '+00:00'))
                created_date = created_dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                pass
        
        last_updated = vps.get('last_updated', 'Never')
        if last_updated != 'Never':
            try:
                updated_dt = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                last_updated = updated_dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                pass

        purge_protected = "❌ No"
        if self.owner_id in admin_data.get('purge_protection', {}).get('protected_users', []):
            purge_protected = "✅ Yes"

        status_text = f"• **State:** {status_emoji} `{status_text}`\n• **Created:** {created_date}\n• **Purge Protected:** {purge_protected}"
        embed.add_field(name="📊 Status", value=status_text, inline=True)

        # Resources section with enhanced details
        resource_text = f"• **CPU:** {vps.get('cpu', 'N/A')} Cores"
        if vps.get('processor'):
            resource_text += f"\n• **Processor:** {vps['processor']}"
        resource_text += f"\n• **RAM:** {vps.get('ram', 'N/A')}\n• **Storage:** {vps.get('storage', 'N/A')}"
        
        embed.add_field(name="⚙️ Resources", value=resource_text, inline=True)

        # Controls section
        controls_text = "Use the buttons below to manage this server"
        embed.add_field(name="🎮 Controls", value=controls_text, inline=False)

        return embed

    def add_action_buttons(self):
        """Add action buttons to the view"""
        # Reinstall button (owner only)
        if not self.is_shared and not self.is_admin:
            reinstall_button = discord.ui.Button(
                label="Reinstall OS", 
                style=discord.ButtonStyle.danger,
                emoji="🔄"
            )
            reinstall_button.callback = lambda inter: self.action_callback(inter, 'reinstall')
            self.add_item(reinstall_button)

        # Control buttons with unique emojis
        start_button = discord.ui.Button(label="Start", style=discord.ButtonStyle.success, emoji="▶️")
        start_button.callback = lambda inter: self.action_callback(inter, 'start')
        
        stop_button = discord.ui.Button(label="Stop", style=discord.ButtonStyle.secondary, emoji="⏹️")
        stop_button.callback = lambda inter: self.action_callback(inter, 'stop')
        
        ssh_button = discord.ui.Button(label="SSH Access", style=discord.ButtonStyle.primary, emoji="🔑")
        ssh_button.callback = lambda inter: self.action_callback(inter, 'tmate')

        self.add_item(start_button)
        self.add_item(stop_button)
        self.add_item(ssh_button)

    async def select_vps(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(embed=create_error_embed("Access Denied", "This is not your VPS!"), ephemeral=True)
            return
        
        self.selected_index = int(self.select.values[0])
        new_embed = self.create_detailed_vps_embed(self.selected_index)
        self.clear_items()
        self.add_action_buttons()
        await interaction.response.edit_message(embed=new_embed, view=self)

    async def action_callback(self, interaction: discord.Interaction, action: str):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(embed=create_error_embed("Access Denied", "This is not your VPS!"), ephemeral=True)
            return

        if self.is_shared:
            vps = vps_data[self.owner_id][self.selected_index]
        else:
            vps = self.vps_list[self.selected_index]
        
        container_name = vps["container_name"]

        if action == 'reinstall':
            if self.is_shared or self.is_admin:
                await interaction.response.send_message(embed=create_error_embed("Access Denied", "Only the VPS owner can reinstall!"), ephemeral=True)
                return

            confirm_embed = create_warning_embed("⚠️ Reinstall Warning",
                f"**DANGER:** This will completely erase all data on VPS `{container_name}` and reinstall with Ubuntu 22.04.\n\n"
                f"**This action cannot be undone!**\n\nDo you want to continue?")

            class ConfirmView(discord.ui.View):
                def __init__(self, parent_view, container_name, vps, owner_id, selected_index):
                    super().__init__(timeout=60)
                    self.parent_view = parent_view
                    self.container_name = container_name
                    self.vps = vps
                    self.owner_id = owner_id
                    self.selected_index = selected_index

                @discord.ui.button(label="✅ Confirm Reinstall", style=discord.ButtonStyle.danger)
                async def confirm(self, interaction: discord.Interaction, item: discord.ui.Button):
                    await interaction.response.defer(ephemeral=True)
                    try:
                        await interaction.followup.send(embed=create_info_embed("🔄 Reinstalling", f"Destroying container `{self.container_name}`..."), ephemeral=True)
                        await execute_lxc(f"lxc delete {self.container_name} --force")

                        await interaction.followup.send(embed=create_info_embed("🚀 Creating", f"Deploying new container `{self.container_name}`..."), ephemeral=True)
                        original_ram = self.vps["ram"]
                        original_cpu = self.vps["cpu"]
                        ram_mb = int(original_ram.replace("GB", "")) * 1024

                        await execute_lxc(f"lxc launch ubuntu:22.04 {self.container_name} --config limits.memory={ram_mb}MB --config limits.cpu={original_cpu} -s dir")

                        self.vps["status"] = "running"
                        self.vps["created_at"] = datetime.now().isoformat()
                        self.vps["last_updated"] = datetime.now().isoformat()
                        save_data()
                        
                        await interaction.followup.send(embed=create_success_embed("✅ Reinstall Complete", f"VPS `{self.container_name}` has been successfully reinstalled with Ubuntu 22.04!"), ephemeral=True)

                        if not self.parent_view.is_shared:
                            await interaction.message.edit(embed=self.parent_view.create_detailed_vps_embed(self.parent_view.selected_index), view=self.parent_view)

                    except Exception as e:
                        await interaction.followup.send(embed=create_error_embed("❌ Reinstall Failed", f"Error: {str(e)}"), ephemeral=True)

                @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
                async def cancel(self, interaction: discord.Interaction, item: discord.ui.Button):
                    await interaction.response.edit_message(embed=self.parent_view.create_detailed_vps_embed(self.parent_view.selected_index), view=self.parent_view)

            await interaction.response.send_message(embed=confirm_embed, view=ConfirmView(self, container_name, vps, self.owner_id, self.selected_index), ephemeral=True)

        elif action == 'start':
            await interaction.response.defer(ephemeral=True)
            try:
                await execute_lxc(f"lxc start {container_name}")
                vps["status"] = "running"
                vps["last_updated"] = datetime.now().isoformat()
                save_data()
                
                # Green embed for successful start
                start_embed = create_embed("✅ VPS Started Successfully", f"VPS `{container_name}` is now online!", 0x00ff88)
                start_embed.add_field(name="Status", value="🟢 **ONLINE** - VPS is running and accessible", inline=False)
                await interaction.followup.send(embed=start_embed, ephemeral=True)
                await interaction.message.edit(embed=self.create_detailed_vps_embed(self.selected_index), view=self)
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("❌ Start Failed", str(e)), ephemeral=True)

        elif action == 'stop':
            await interaction.response.defer(ephemeral=True)
            try:
                await execute_lxc(f"lxc stop {container_name}", timeout=120)
                vps["status"] = "stopped"
                vps["last_updated"] = datetime.now().isoformat()
                save_data()
                
                # Red embed for successful stop
                stop_embed = create_embed("⏸️ VPS Stopped Successfully", f"VPS `{container_name}` is now offline!", 0xff3366)
                stop_embed.add_field(name="Status", value="🔴 **OFFLINE** - VPS has been stopped", inline=False)
                await interaction.followup.send(embed=stop_embed, ephemeral=True)
                await interaction.message.edit(embed=self.create_detailed_vps_embed(self.selected_index), view=self)
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("❌ Stop Failed", str(e)), ephemeral=True)

        elif action == 'tmate':
            await interaction.response.send_message(embed=create_info_embed("🔑 SSH Access", "Generating SSH connection..."), ephemeral=True)

            try:
                # Check if tmate exists
                check_proc = await asyncio.create_subprocess_exec(
                    "lxc", "exec", container_name, "--", "which", "tmate",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await check_proc.communicate()

                if check_proc.returncode != 0:
                    await interaction.followup.send(embed=create_info_embed("📦 Installing SSH", "Installing tmate for SSH access..."), ephemeral=True)
                    await execute_lxc(f"lxc exec {container_name} -- sudo apt-get update -y")
                    await execute_lxc(f"lxc exec {container_name} -- sudo apt-get install tmate -y")
                    await interaction.followup.send(embed=create_success_embed("✅ Installation Complete", "SSH service installed successfully!"), ephemeral=True)

                # Start tmate with unique session name
                session_name = f"session-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                await execute_lxc(f"lxc exec {container_name} -- tmate -S /tmp/{session_name}.sock new-session -d")
                await asyncio.sleep(3)

                # Get SSH link
                ssh_proc = await asyncio.create_subprocess_exec(
                    "lxc", "exec", container_name, "--", "tmate", "-S", f"/tmp/{session_name}.sock", "display", "-p", "#{tmate_ssh}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await ssh_proc.communicate()
                ssh_url = stdout.decode().strip() if stdout else None

                if ssh_url:
                    try:
                        ssh_embed = create_success_embed("🔑 SSH Access Generated", f"SSH connection for VPS `{container_name}`")
                        ssh_embed.add_field(name="📋 SSH Command", value=f"```bash\n{ssh_url}\n```", inline=False)
                        ssh_embed.add_field(name="🛡️ Security Notice", value="• This link is temporary and secure\n• Do not share with others\n• Session will auto-expire", inline=False)
                        ssh_embed.add_field(name="📝 Session Details", value=f"**Session ID:** `{session_name}`\n**Created:** {datetime.now().strftime('%H:%M:%S')}", inline=False)
                        await interaction.user.send(embed=ssh_embed)
                        await interaction.followup.send(embed=create_success_embed("📨 SSH Details Sent", f"Check your DMs for SSH access!\n**Session:** `{session_name}`"), ephemeral=True)
                    except discord.Forbidden:
                        await interaction.followup.send(embed=create_error_embed("❌ DM Failed", "Please enable DMs to receive SSH credentials!"), ephemeral=True)
                else:
                    error_msg = stderr.decode().strip() if stderr else "Failed to generate SSH link"
                    await interaction.followup.send(embed=create_error_embed("❌ SSH Generation Failed", error_msg), ephemeral=True)
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("❌ SSH Error", str(e)), ephemeral=True)

# Enhanced manage command
@bot.command(name='manage')
@maintenance_check()
async def manage_vps(ctx, user: discord.Member = None):
    """Manage your VPS or another user's VPS (Admin only)"""
    system_stats['commands_executed'] += 1
    
    if user:
        # Only admins can manage other users' VPS
        if not (str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", [])):
            await ctx.send(embed=create_error_embed("Access Denied", "Only admins can manage other users' VPS."))
            return
        
        user_id = str(user.id)
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            await ctx.send(embed=create_error_embed("No VPS Found", f"{user.mention} doesn't have any VPS."))
            return
        
        # Update VPS statuses before displaying
        for vps in vps_list:
            if vps.get('container_name'):
                current_status = await get_vps_status(vps['container_name'])
                if current_status != 'error':
                    vps['status'] = current_status
                    vps['last_updated'] = datetime.now().isoformat()
        save_data()
        
        view = EnhancedManageView(str(ctx.author.id), vps_list, is_admin=True, owner_id=user_id)
        await ctx.send(embed=view.initial_embed, view=view)
    else:
        # User managing their own VPS
        user_id = str(ctx.author.id)
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            embed = create_embed("No VPS Found", "You don't have any VPS yet.", 0xff3366)
            embed.add_field(name="🚀 Get Started", value="• `.plans` - View available plans\n• `.freeplans` - Check free options\n• `.buywc <plan>` - Purchase VPS\n• `.buyc` - Buy credits", inline=False)
            await ctx.send(embed=embed)
            return
        
        # Update VPS statuses before displaying
        for vps in vps_list:
            if vps.get('container_name'):
                current_status = await get_vps_status(vps['container_name'])
                if current_status != 'error':
                    vps['status'] = current_status
                    vps['last_updated'] = datetime.now().isoformat()
        save_data()
        
        view = EnhancedManageView(user_id, vps_list)
        await ctx.send(embed=view.initial_embed, view=view)

# Purge protection commands
@bot.command(name='dontpurgevps')
@is_admin()
async def protect_vps(ctx, user: discord.Member):
    """Protect VPS/user from purge (Admin only)"""
    user_id = str(user.id)
    
    if 'purge_protection' not in admin_data:
        admin_data['purge_protection'] = {"enabled": True, "protected_users": [], "protected_vps": 0}
    
    protected_users = admin_data['purge_protection'].get('protected_users', [])
    
    if user_id in protected_users:
        await ctx.send(embed=create_warning_embed("Already Protected", f"{user.mention} is already protected from purges!"))
        return
    
    protected_users.append(user_id)
    admin_data['purge_protection']['protected_users'] = protected_users
    admin_data['purge_protection']['protected_vps'] = admin_data['purge_protection'].get('protected_vps', 0) + len(vps_data.get(user_id, []))
    
    save_data()
    
    embed = create_success_embed("🛡️ Purge Protection Enabled", f"Protection activated for {user.mention}")
    embed.add_field(name="Protected", value=f"• User: {user.mention}\n• VPS Count: {len(vps_data.get(user_id, []))}\n• Status: ✅ Active", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='dontpurgevpsr')
@is_admin()
async def remove_vps_protection(ctx, user: discord.Member):
    """Remove VPS protection from user (Admin only)"""
    user_id = str(user.id)
    
    if 'purge_protection' not in admin_data:
        admin_data['purge_protection'] = {"enabled": True, "protected_users": [], "protected_vps": 0}
    
    protected_users = admin_data['purge_protection'].get('protected_users', [])
    
    if user_id not in protected_users:
        await ctx.send(embed=create_error_embed("Not Protected", f"{user.mention} is not currently protected from purges!"))
        return
    
    protected_users.remove(user_id)
    admin_data['purge_protection']['protected_users'] = protected_users
    admin_data['purge_protection']['protected_vps'] = admin_data['purge_protection'].get('protected_vps', 0) - len(vps_data.get(user_id, []))
    
    save_data()
    
    embed = create_info_embed("🛡️ Purge Protection Removed", f"Protection removed from {user.mention}")
    embed.add_field(name="Unprotected", value=f"• User: {user.mention}\n• VPS Count: {len(vps_data.get(user_id, []))}\n• Status: ❌ Unprotected", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='purgestart')
@is_main_admin()
async def start_purge(ctx):
    """Start purge system (Main Admin only)"""
    admin_data['purge_protection']['enabled'] = True
    save_data()
    
    embed = create_success_embed("🛡️ Purge System Started", "Purge protection system is now active!")
    embed.add_field(name="Status", value="✅ Active\n🔄 Monitoring enabled\n⏰ Every 5 minutes", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='purgestop')
@is_main_admin()
async def stop_purge(ctx):
    """Stop purge system (Main Admin only)"""
    admin_data['purge_protection']['enabled'] = False
    save_data()
    
    embed = create_warning_embed("🛡️ Purge System Stopped", "Purge protection system is now inactive!")
    embed.add_field(name="Status", value="❌ Inactive\n⏸️ Monitoring disabled\n⚠️ VPS are unprotected", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='purgeinfo')
@is_admin()
async def purge_info(ctx):
    """Show purge system information"""
    purge_data = admin_data.get('purge_protection', {})
    is_enabled = purge_data.get('enabled', False)
    protected_users = len(purge_data.get('protected_users', []))
    protected_vps = purge_data.get('protected_vps', 0)
    
    status_text = "✅ Active" if is_enabled else "❌ Inactive"
    
    embed = create_embed("🛡️ Purge System Information", "Current purge protection status", 0x1a1a1a)
    embed.add_field(name="System Status", value=f"**Status:** {status_text}\n**Protected Users:** {protected_users}\n**Protected VPS:** {protected_vps}\n**Interval:** Every 5 minutes", inline=False)
    
    system_info = get_system_info()
    if system_info:
        embed.add_field(name="📊 System Information", value=f"**Developer:** XeloraCloud\n**Processor:** Ryzen 9 7900\n**Network:** IPv6 Only (no IPv4)\n**Ticket Required:** ✅ Yes", inline=False)
    
    await ctx.send(embed=embed)

# Free Plans System
@bot.command(name='freeplans')
@maintenance_check()
async def free_plans(ctx):
    """View free plans (boost/invite)"""
    if not FREE_PLAN_ENABLED:
        await ctx.send(embed=create_error_embed("Free Plans Disabled", "Free plans are currently not available."))
        return
    
    embed = create_embed("🆓 Free VPS Plans", "Get free VPS through server boosts or invites!", 0x1a1a1a)
    
    # Boost plans
    boost_plans = [
        {"name": "Boost Starter", "boosts": 1, "ram": "2GB", "cpu": "1", "storage": "5GB"},
        {"name": "Boost Basic", "boosts": 2, "ram": "4GB", "cpu": "1", "storage": "10GB"},
        {"name": "Boost Pro", "boosts": 3, "ram": "6GB", "cpu": "2", "storage": "15GB"}
    ]
    
    boost_text = ""
    for plan in boost_plans:
        boost_text += f"**{plan['name']}** ({plan['boosts']} boost{'s' if plan['boosts'] > 1 else ''})\n"
        boost_text += f"• RAM: {plan['ram']} • CPU: {plan['cpu']} Core{'s' if plan['cpu'] != '1' else ''} • Storage: {plan['storage']}\n\n"
    
    embed.add_field(name="🚀 Server Boost Plans", value=boost_text, inline=False)
    
    # Invite plans
    invite_plans = [
        {"name": "Invite Starter", "invites": 5, "ram": "1GB", "cpu": "1", "storage": "5GB"},
        {"name": "Invite Basic", "invites": 10, "ram": "2GB", "cpu": "1", "storage": "8GB"},
        {"name": "Invite Advanced", "invites": 20, "ram": "4GB", "cpu": "1", "storage": "12GB"}
    ]
    
    invite_text = ""
    for plan in invite_plans:
        invite_text += f"**{plan['name']}** ({plan['invites']} invites)\n"
        invite_text += f"• RAM: {plan['ram']} • CPU: {plan['cpu']} Core • Storage: {plan['storage']}\n\n"
    
    embed.add_field(name="👥 Invite Plans", value=invite_text, inline=False)
    
    embed.add_field(name="📋 How to Claim", value="• Boost this server to unlock boost plans\n• Invite friends to unlock invite plans\n• Contact admin to verify and claim\n• One free VPS per user maximum", inline=False)
    
    await ctx.send(embed=embed)

# Advanced Admin Commands
@bot.command(name='addpaid')
@is_admin()
async def add_paid_plan(ctx, name: str, price: int, ram: str, cpu: str, storage: str, *, description: str):
    """Add paid plan"""
    if 'custom_plans' not in admin_data:
        admin_data['custom_plans'] = {'paid': [], 'boost': [], 'invite': []}
    
    plan = {
        "name": name,
        "type": "paid",
        "price": price,
        "ram": ram,
        "cpu": cpu,
        "storage": storage,
        "description": description,
        "created_at": datetime.now().isoformat()
    }
    
    admin_data['custom_plans']['paid'].append(plan)
    save_data()
    
    embed = create_success_embed("💎 Paid Plan Added", f"Successfully added paid plan: **{name}**")
    embed.add_field(name="Plan Details", value=f"**Price:** {price} credits\n**RAM:** {ram}\n**CPU:** {cpu} cores\n**Storage:** {storage}\n**Description:** {description}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='addboost')
@is_admin()
async def add_boost_plan(ctx, name: str, boosts: int, ram: str, cpu: str, storage: str):
    """Add boost plan"""
    if 'custom_plans' not in admin_data:
        admin_data['custom_plans'] = {'paid': [], 'boost': [], 'invite': []}
    
    plan = {
        "name": name,
        "type": "boost",
        "boosts_required": boosts,
        "ram": ram,
        "cpu": cpu,
        "storage": storage,
        "created_at": datetime.now().isoformat()
    }
    
    admin_data['custom_plans']['boost'].append(plan)
    save_data()
    
    embed = create_success_embed("🚀 Boost Plan Added", f"Successfully added boost plan: **{name}**")
    embed.add_field(name="Plan Details", value=f"**Boosts Required:** {boosts}\n**RAM:** {ram}\n**CPU:** {cpu} cores\n**Storage:** {storage}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='addinvite')
@is_admin()
async def add_invite_plan(ctx, name: str, invites: int, ram: str, cpu: str, storage: str):
    """Add invite plan"""
    if 'custom_plans' not in admin_data:
        admin_data['custom_plans'] = {'paid': [], 'boost': [], 'invite': []}
    
    plan = {
        "name": name,
        "type": "invite",
        "invites_required": invites,
        "ram": ram,
        "cpu": cpu,
        "storage": storage,
        "created_at": datetime.now().isoformat()
    }
    
    admin_data['custom_plans']['invite'].append(plan)
    save_data()
    
    embed = create_success_embed("👥 Invite Plan Added", f"Successfully added invite plan: **{name}**")
    embed.add_field(name="Plan Details", value=f"**Invites Required:** {invites}\n**RAM:** {ram}\n**CPU:** {cpu} cores\n**Storage:** {storage}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='removeplan')
@is_admin()
async def remove_plan(ctx, plan_type: str, name: str):
    """Remove plan"""
    if plan_type not in ['paid', 'boost', 'invite']:
        await ctx.send(embed=create_error_embed("Invalid Type", "Plan type must be: paid, boost, or invite"))
        return
    
    if 'custom_plans' not in admin_data:
        admin_data['custom_plans'] = {'paid': [], 'boost': [], 'invite': []}
    
    plans = admin_data['custom_plans'][plan_type]
    plan_found = None
    
    for i, plan in enumerate(plans):
        if plan['name'].lower() == name.lower():
            plan_found = plans.pop(i)
            break
    
    if not plan_found:
        await ctx.send(embed=create_error_embed("Plan Not Found", f"No {plan_type} plan named '{name}' found."))
        return
    
    save_data()
    
    embed = create_success_embed("🗑️ Plan Removed", f"Successfully removed {plan_type} plan: **{name}**")
    await ctx.send(embed=embed)

@bot.command(name='editplans')
@is_admin()
async def edit_plans(ctx):
    """Edit VPS plans"""
    if 'custom_plans' not in admin_data:
        admin_data['custom_plans'] = {'paid': [], 'boost': [], 'invite': []}
    
    embed = create_embed("📝 Plan Editor", "Current custom plans configuration", 0x1a1a1a)
    
    # Paid plans
    paid_plans = admin_data['custom_plans']['paid']
    if paid_plans:
        paid_text = "\n".join([f"• **{p['name']}** - {p['price']} credits - {p['ram']} RAM" for p in paid_plans])
    else:
        paid_text = "No custom paid plans"
    embed.add_field(name="💎 Paid Plans", value=paid_text, inline=False)
    
    # Boost plans
    boost_plans = admin_data['custom_plans']['boost']
    if boost_plans:
        boost_text = "\n".join([f"• **{p['name']}** - {p['boosts_required']} boosts - {p['ram']} RAM" for p in boost_plans])
    else:
        boost_text = "No custom boost plans"
    embed.add_field(name="🚀 Boost Plans", value=boost_text, inline=False)
    
    # Invite plans
    invite_plans = admin_data['custom_plans']['invite']
    if invite_plans:
        invite_text = "\n".join([f"• **{p['name']}** - {p['invites_required']} invites - {p['ram']} RAM" for p in invite_plans])
    else:
        invite_text = "No custom invite plans"
    embed.add_field(name="👥 Invite Plans", value=invite_text, inline=False)
    
    embed.add_field(name="📋 Commands", value="• `.addpaid <name> <price> <ram> <cpu> <storage> <description>`\n• `.addboost <name> <boosts> <ram> <cpu> <storage>`\n• `.addinvite <name> <invites> <ram> <cpu> <storage>`\n• `.removeplan <type> <name>`", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='suspendvps')
@is_admin()
async def suspend_vps(ctx, user: discord.Member):
    """Suspend a VPS (Interactive)"""
    user_id = str(user.id)
    if user_id not in vps_data or not vps_data[user_id]:
        await ctx.send(embed=create_error_embed("No VPS Found", f"{user.mention} doesn't have any VPS."))
        return
    
    vps_list = vps_data[user_id]
    if len(vps_list) == 1:
        # Auto-suspend the only VPS
        vps = vps_list[0]
        container_name = vps['container_name']
        
        try:
            await execute_lxc(f"lxc stop {container_name} --force")
            vps['status'] = 'suspended'
            vps['suspended_at'] = datetime.now().isoformat()
            vps['suspended_by'] = str(ctx.author.id)
            save_data()
            
            # Yellow embed for suspension
            embed = create_embed("⏸️ VPS Suspended", f"VPS for {user.mention} has been suspended", 0xffaa00)
            embed.add_field(name="Status", value="🟡 **SUSPENDED** - VPS access temporarily disabled", inline=False)
            embed.add_field(name="Details", value=f"**Container:** `{container_name}`\n**Suspended by:** {ctx.author.mention}\n**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", inline=False)
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(embed=create_error_embed("Suspension Failed", f"Error: {str(e)}"))
    else:
        # Multiple VPS - show selection
        options = [
            discord.SelectOption(
                label=f"VPS {i+1} - {v.get('plan', 'Custom')}",
                description=f"Status: {v.get('status', 'unknown')} | {v.get('ram', 'N/A')} RAM",
                value=str(i)
            ) for i, v in enumerate(vps_list)
        ]
        
        class SuspendView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.select = discord.ui.Select(placeholder="Select VPS to suspend", options=options)
                self.select.callback = self.suspend_selected
                self.add_item(self.select)
            
            async def suspend_selected(self, interaction: discord.Interaction):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message("This is not your selection!", ephemeral=True)
                    return
                
                selected_index = int(self.select.values[0])
                vps = vps_list[selected_index]
                container_name = vps['container_name']
                
                try:
                    await execute_lxc(f"lxc stop {container_name} --force")
                    vps['status'] = 'suspended'
                    vps['suspended_at'] = datetime.now().isoformat()
                    vps['suspended_by'] = str(ctx.author.id)
                    save_data()
                    
                    # Yellow embed for suspension
                    embed = create_embed("⏸️ VPS Suspended", f"VPS {selected_index + 1} for {user.mention} has been suspended", 0xffaa00)
                    embed.add_field(name="Status", value="🟡 **SUSPENDED** - VPS access temporarily disabled", inline=False)
                    embed.add_field(name="Details", value=f"**Container:** `{container_name}`\n**Plan:** {vps.get('plan', 'Custom')}\n**Suspended by:** {ctx.author.mention}", inline=False)
                    await interaction.response.edit_message(embed=embed, view=None)
                    
                except Exception as e:
                    await interaction.response.edit_message(embed=create_error_embed("Suspension Failed", f"Error: {str(e)}"), view=None)
        
        embed = create_embed("⏸️ Suspend VPS", f"Select which VPS to suspend for {user.mention}", 0xffaa00)
        await ctx.send(embed=embed, view=SuspendView())

@bot.command(name='unsuspend')
@is_admin()
async def unsuspend_vps(ctx, user: discord.Member, vps_number: int):
    """Unsuspend a VPS"""
    user_id = str(user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number or user doesn't have a VPS."))
        return
    
    vps = vps_data[user_id][vps_number - 1]
    if vps.get('status') != 'suspended':
        await ctx.send(embed=create_error_embed("Not Suspended", "This VPS is not currently suspended."))
        return
    
    container_name = vps['container_name']
    
    try:
        await execute_lxc(f"lxc start {container_name}")
        vps['status'] = 'running'
        if 'suspended_at' in vps:
            del vps['suspended_at']
        if 'suspended_by' in vps:
            del vps['suspended_by']
        vps['last_updated'] = datetime.now().isoformat()
        save_data()
        
        # Green embed for successful unsuspension
        embed = create_embed("▶️ VPS Restored Successfully", f"VPS {vps_number} for {user.mention} is now online!", 0x00ff88)
        embed.add_field(name="Status", value="🟢 **ONLINE** - VPS access fully restored", inline=False)
        embed.add_field(name="Details", value=f"**Container:** `{container_name}`\n**Plan:** {vps.get('plan', 'Custom')}\n**Restored by:** {ctx.author.mention}", inline=False)
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(embed=create_error_embed("Restoration Failed", f"Error: {str(e)}"))

@bot.command(name='upgradevps')
@is_admin()
async def upgrade_vps(ctx, user: discord.Member, vps_number: int, ram: int, cpu: int):
    """Upgrade VPS resources"""
    user_id = str(user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number or user doesn't have a VPS."))
        return
    
    if ram <= 0 or cpu <= 0:
        await ctx.send(embed=create_error_embed("Invalid Resources", "RAM and CPU must be positive values."))
        return
    
    vps = vps_data[user_id][vps_number - 1]
    container_name = vps['container_name']
    old_ram = vps.get('ram', 'Unknown')
    old_cpu = vps.get('cpu', 'Unknown')
    
    try:
        # Stop VPS first
        await execute_lxc(f"lxc stop {container_name}")
        
        # Update resources
        ram_mb = ram * 1024
        await execute_lxc(f"lxc config set {container_name} limits.memory {ram_mb}MB")
        await execute_lxc(f"lxc config set {container_name} limits.cpu {cpu}")
        
        # Start VPS
        await execute_lxc(f"lxc start {container_name}")
        
        # Update database
        vps['ram'] = f"{ram}GB"
        vps['cpu'] = str(cpu)
        vps['status'] = 'running'
        vps['last_updated'] = datetime.now().isoformat()
        vps['upgraded_at'] = datetime.now().isoformat()
        vps['upgraded_by'] = str(ctx.author.id)
        save_data()
        
        # Green embed for successful upgrade
        embed = create_embed("⬆️ VPS Upgraded Successfully", f"VPS {vps_number} for {user.mention} has been upgraded and is online!", 0x00ff88)
        embed.add_field(name="Resource Changes", value=f"**RAM:** {old_ram} → {ram}GB\n**CPU:** {old_cpu} → {cpu} cores", inline=False)
        embed.add_field(name="Status", value="🟢 **ONLINE** - VPS running with new resources", inline=False)
        embed.add_field(name="Details", value=f"**Container:** `{container_name}`\n**Upgraded by:** {ctx.author.mention}", inline=False)
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(embed=create_error_embed("Upgrade Failed", f"Error: {str(e)}"))

@bot.command(name='stopall')
@is_admin()
async def stop_all_vps(ctx, *, reason: str = "Administrative maintenance"):
    """Stop all VPS"""
    confirm_embed = create_warning_embed("⚠️ Stop All VPS", 
        f"**WARNING:** This will stop ALL running VPS containers!\n\n"
        f"**Reason:** {reason}\n\n"
        f"This action will affect all users. Continue?")

    class ConfirmView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="✅ Confirm Stop All", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, item: discord.ui.Button):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("This is not your action!", ephemeral=True)
                return
            
            await interaction.response.defer()
            
            try:
                await interaction.followup.send(embed=create_info_embed("🛑 Stopping All VPS", "Stopping all containers..."))
                await execute_lxc("lxc stop --all --force")
                
                # Update all VPS status in database
                stopped_count = 0
                for user_id, vps_list in vps_data.items():
                    for vps in vps_list:
                        if vps.get('status') == 'running':
                            vps['status'] = 'stopped'
                            vps['last_updated'] = datetime.now().isoformat()
                            vps['stopped_reason'] = reason
                            vps['stopped_by'] = str(ctx.author.id)
                            stopped_count += 1
                
                save_data()
                
                embed = create_success_embed("🛑 All VPS Stopped", f"Successfully stopped {stopped_count} running VPS")
                embed.add_field(name="Details", value=f"**Reason:** {reason}\n**Stopped by:** {ctx.author.mention}\n**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", inline=False)
                await interaction.followup.send(embed=embed)
                
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("❌ Stop Failed", f"Error: {str(e)}"))

        @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, item: discord.ui.Button):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("This is not your action!", ephemeral=True)
                return
            await interaction.response.edit_message(embed=create_info_embed("Cancelled", "Stop all operation cancelled."), view=None)

    await ctx.send(embed=confirm_embed, view=ConfirmView())

@bot.command(name='userinfo')
@is_admin()
async def user_info(ctx, user: discord.Member):
    """Get user information"""
    user_id = str(user.id)
    user_vps = vps_data.get(user_id, [])
    user_credits = user_data.get(user_id, {}).get('credits', 0)
    
    # Calculate user stats
    running_vps = sum(1 for vps in user_vps if vps.get('status') == 'running')
    stopped_vps = len(user_vps) - running_vps
    
    is_protected = user_id in admin_data.get('purge_protection', {}).get('protected_users', [])
    is_admin_user = user_id in admin_data.get('admins', []) or user_id == str(MAIN_ADMIN_ID)
    
    embed = create_embed("👤 User Information", f"Detailed information for {user.mention}", 0x1a1a1a)
    
    # Basic info
    embed.add_field(name="📊 Overview", 
        value=f"**Username:** {user.name}\n**ID:** {user_id}\n**Credits:** {user_credits:,}\n**Admin:** {'✅ Yes' if is_admin_user else '❌ No'}", 
        inline=True)
    
    # VPS stats
    embed.add_field(name="🖥️ VPS Statistics", 
        value=f"**Total VPS:** {len(user_vps)}\n**Running:** {running_vps}\n**Stopped:** {stopped_vps}\n**Protected:** {'✅ Yes' if is_protected else '❌ No'}", 
        inline=True)
    
    # VPS list
    if user_vps:
        vps_list = []
        for i, vps in enumerate(user_vps):
            status_emoji = "🟢" if vps.get('status') == 'running' else "🔴" if vps.get('status') == 'stopped' else "⏸️"
            vps_list.append(f"{status_emoji} **VPS {i+1}:** `{vps.get('container_name', 'Unknown')}` ({vps.get('plan', 'Custom')})")
        
        embed.add_field(name="🖥️ User's VPS", value="\n".join(vps_list), inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='maintenance')
@is_main_admin()
async def maintenance_mode(ctx, mode: str = None):
    """Toggle maintenance mode (Main Admin only)"""
    global MAINTENANCE_MODE
    
    if mode is None:
        status = "🟡 Active" if MAINTENANCE_MODE else "🟢 Normal"
        embed = create_embed("🔧 Maintenance Mode", f"Current status: {status}", 0x1a1a1a)
        embed.add_field(name="Usage", value="`.maintenance on` - Enable maintenance mode\n`.maintenance off` - Disable maintenance mode", inline=False)
        await ctx.send(embed=embed)
        return
    
    if mode.lower() == 'on':
        MAINTENANCE_MODE = True
        embed = create_warning_embed("🔧 Maintenance Mode Enabled", "The bot is now in maintenance mode. Only main admin can use commands.")
    elif mode.lower() == 'off':
        MAINTENANCE_MODE = False
        embed = create_success_embed("🔧 Maintenance Mode Disabled", "The bot is back to normal operation. All users can use commands.")
    else:
        await ctx.send(embed=create_error_embed("Invalid Option", "Use `on` or `off`"))
        return
    
    await ctx.send(embed=embed)

# Enhanced VPS creation and core commands from original
@bot.command(name='create')
@is_admin()
@maintenance_check()
async def create_vps(ctx, user: discord.Member, ram: int, cpu: int, disk: int):
    """Create a custom VPS for a user (Admin only)"""
    system_stats['commands_executed'] += 1
    
    if ram <= 0 or cpu <= 0 or disk <= 0:
        await ctx.send(embed=create_error_embed("Invalid Specs", "RAM, CPU, and Disk must be positive integers."))
        return

    user_id = str(user.id)
    if user_id not in vps_data:
        vps_data[user_id] = []

    vps_count = len(vps_data[user_id]) + 1
    username = user.name.replace(" ", "_").lower()
    container_name = f"vps-{username}-{vps_count}"
    ram_mb = ram * 1024
    disk_gb = disk

    # Enhanced creation embed
    creation_embed = create_info_embed("🚀 Deploying VPS", f"Creating custom VPS for {user.mention}...")
    creation_embed.add_field(name="📊 Specifications", value=f"**RAM:** {ram}GB\n**CPU:** {cpu} Cores\n**Storage:** {disk_gb}GB\n**Container:** `{container_name}`", inline=False)
    creation_embed.add_field(name="🔄 Status", value="⏳ **Initializing container...**", inline=False)
    creation_msg = await ctx.send(embed=creation_embed)

    try:
        # Update creation status
        creation_embed.set_field_at(1, name="🔄 Status", value="📦 **Launching Ubuntu 22.04...**", inline=False)
        await creation_msg.edit(embed=creation_embed)
        
        await execute_lxc(f"lxc launch ubuntu:22.04 {container_name} --config limits.memory={ram_mb}MB --config limits.cpu={cpu} -s dir")

        # Update creation status
        creation_embed.set_field_at(1, name="🔄 Status", value="⚙️ **Configuring resources...**", inline=False)
        await creation_msg.edit(embed=creation_embed)

        vps_info = {
            "container_name": container_name,
            "ram": f"{ram}GB",
            "cpu": str(cpu),
            "storage": f"{disk_gb}GB",
            "status": "running",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "shared_with": [],
            "plan": "Custom",
            "created_by": str(ctx.author.id)
        }
        vps_data[user_id].append(vps_info)
        system_stats['total_vps_created'] += 1
        save_data()

        # Get or create VPS role and assign to user
        if ctx.guild:
            vps_role = await get_or_create_vps_role(ctx.guild)
            if vps_role:
                try:
                    await user.add_roles(vps_role, reason="VPS ownership granted")
                except discord.Forbidden:
                    logger.warning(f"Failed to assign VPS role to {user.name}")

        # Final success embed with enhanced formatting
        success_embed = create_success_embed("✅ XeloraCloud - XeloraCloud VPS Created Successfully", "")
        success_embed.set_thumbnail(url="https://i.imghippo.com/files/Mf6255KwU.png")
        
        # Owner section
        success_embed.add_field(name="👤 Owner", value=f"{user.mention} • Piyush", inline=True)
        success_embed.add_field(name="🆔 VPS ID", value=f"#{vps_count}", inline=True)
        success_embed.add_field(name="📦 Container", value=f"`{container_name}`", inline=True)
        
        # Resources section
        resources_text = f"**RAM:** {ram}GB\n**CPU:** {cpu} Cores\n**Storage:** {disk_gb}GB"
        success_embed.add_field(name="⚙️ Resources", value=resources_text, inline=False)
        
        # OS section
        success_embed.add_field(name="💿 OS", value="ubuntu.24.04", inline=False)
        
        # Features section
        success_embed.add_field(name="✨ Features", value="Nesting: Privileged, FUSE, Kernel Modules (Docker Ready)", inline=False)
        
        # Disk Note
        success_embed.add_field(name="💾 Disk Note", value="Run `sudo resize2fs /` inside VPS if needed to expand filesystem.", inline=False)
        
        # Footer with branding
        success_embed.set_footer(text=f"XeloraCloud VPS Manager • Powered by Cloud Technology • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 
                                icon_url="https://i.imghippo.com/files/Mf6255KwU.png")

        await creation_msg.edit(embed=success_embed)

        # Send enhanced DM to user
        try:
            dm_embed = create_success_embed("🎉 Your VPS is Ready!", f"Your custom VPS has been successfully deployed!")
            dm_embed.add_field(name="📊 VPS Details", 
                value=f"**VPS ID:** #{vps_count}\n**Container:** `{container_name}`\n**Plan:** Custom\n**RAM:** {ram}GB\n**CPU:** {cpu} Cores\n**Storage:** {disk_gb}GB\n**OS:** Ubuntu 24.04", 
                inline=False)
            dm_embed.add_field(name="🚀 Next Steps", 
                value="• Use `.manage` to control your VPS\n• Click **SSH Access** to get terminal access\n• Use **Start/Stop** buttons to manage power\n• **Reinstall OS** button for fresh setup", 
                inline=False)
            dm_embed.add_field(name="💡 Pro Tips", 
                value="• VPS auto-starts after creation\n• SSH credentials are sent privately\n• Use `.help` for all available commands", 
                inline=False)
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            await ctx.send(embed=create_info_embed("📧 Notification", f"Couldn't send DM to {user.mention}. Please ensure DMs are enabled for setup instructions."))

    except Exception as e:
        error_embed = create_error_embed("❌ VPS Creation Failed", f"Error creating VPS: {str(e)}")
        await creation_msg.edit(embed=error_embed)

@bot.command(name='buywc')
@maintenance_check()
async def buy_with_credits(ctx, plan: str, processor: str = "Intel"):
    """Buy a VPS with credits"""
    system_stats['commands_executed'] += 1
    user_id = str(ctx.author.id)
    
    prices = {
        "Starter": {"Intel": 42, "AMD": 83},
        "Basic": {"Intel": 96, "AMD": 164},
        "Standard": {"Intel": 192, "AMD": 320},
        "Pro": {"Intel": 220, "AMD": 340}
    }
    plans = {
        "Starter": {"ram": "4GB", "cpu": "1", "storage": "10GB"},
        "Basic": {"ram": "8GB", "cpu": "1", "storage": "10GB"},
        "Standard": {"ram": "12GB", "cpu": "2", "storage": "10GB"},
        "Pro": {"ram": "16GB", "cpu": "2", "storage": "10GB"}
    }

    if plan not in prices:
        await ctx.send(embed=create_error_embed("Invalid Plan", "Available plans: Starter, Basic, Standard, Pro"))
        return
    if processor not in ["Intel", "AMD"]:
        await ctx.send(embed=create_error_embed("Invalid Processor", "Choose: Intel or AMD"))
        return

    cost = prices[plan][processor]
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}

    if user_data[user_id]["credits"] < cost:
        needed = cost - user_data[user_id]["credits"]
        embed = create_error_embed("💰 Insufficient Credits", f"You need **{cost}** credits but have **{user_data[user_id]['credits']}**")
        embed.add_field(name="Required", value=f"**Missing:** {needed} credits\n**Total Cost:** {cost} credits", inline=False)
        embed.add_field(name="💳 Get Credits", value="Use `.buyc` to purchase credits", inline=False)
        await ctx.send(embed=embed)
        return

    user_data[user_id]["credits"] -= cost
    if user_id not in vps_data:
        vps_data[user_id] = []
    
    vps_count = len(vps_data[user_id]) + 1
    username = ctx.author.name.replace(" ", "_").lower()
    container_name = f"vps-{username}-{vps_count}"
    ram_str = plans[plan]["ram"]
    cpu_str = plans[plan]["cpu"]
    ram_mb = int(ram_str.replace("GB", "")) * 1024

    # Enhanced purchase process
    purchase_embed = create_info_embed("💳 Processing Purchase", f"Purchasing {plan} VPS with {processor} processor...")
    purchase_embed.add_field(name="💰 Transaction", value=f"**Plan:** {plan}\n**Processor:** {processor}\n**Cost:** {cost} credits\n**Remaining:** {user_data[user_id]['credits']} credits", inline=False)
    purchase_msg = await ctx.send(embed=purchase_embed)

    try:
        # Update status
        purchase_embed.set_field_at(0, name="🚀 Deployment", value="**Status:** Launching container...\n**Plan:** " + plan + f"\n**Processor:** {processor}\n**Container:** `{container_name}`", inline=False)
        await purchase_msg.edit(embed=purchase_embed)
        
        await execute_lxc(f"lxc launch ubuntu:22.04 {container_name} --config limits.memory={ram_mb}MB --config limits.cpu={cpu_str} -s dir")
        
        vps_info = {
            "plan": plan,
            "container_name": container_name,
            "ram": ram_str,
            "cpu": cpu_str,
            "storage": plans[plan]["storage"],
            "status": "running",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "processor": processor,
            "shared_with": [],
            "purchased_with": "credits",
            "cost": cost
        }
        vps_data[user_id].append(vps_info)
        system_stats['total_vps_created'] += 1
        save_data()

        # Get or create VPS role and assign to user
        if ctx.guild:
            vps_role = await get_or_create_vps_role(ctx.guild)
            if vps_role:
                try:
                    await ctx.author.add_roles(vps_role, reason="VPS purchase completed")
                except discord.Forbidden:
                    logger.warning(f"Failed to assign VPS role to {ctx.author.name}")

        # Enhanced success message like in screenshot
        success_embed = create_success_embed("🎉 XeloraCloud - XeloraCloud VPS Created Successfully", "")
        success_embed.set_thumbnail(url="https://i.imghippo.com/files/Mf6255KwU.png")
        
        success_embed.add_field(name="👤 Owner", value=f"{ctx.author.mention} • Piyush", inline=True)
        success_embed.add_field(name="🆔 VPS ID", value=f"#{vps_count}", inline=True) 
        success_embed.add_field(name="📦 Container", value=f"`{container_name}`", inline=True)
        
        success_embed.add_field(name="⚙️ Resources", value=f"**RAM:** {ram_str}\n**CPU:** {cpu_str} Cores\n**Storage:** {plans[plan]['storage']}", inline=False)
        success_embed.add_field(name="💿 OS", value="images.debian/11", inline=False)
        success_embed.add_field(name="✨ Features", value="Nesting, Privileged, FUSE, Kernel Modules (Docker Ready)", inline=False)
        success_embed.add_field(name="💾 Disk Note", value="Run `sudo resize2fs /` inside VPS if needed to expand filesystem.", inline=False)
        
        success_embed.set_footer(text=f"XeloraCloud VPS Manager • Powered by Cloud Technology • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 
                                icon_url="https://i.imghippo.com/files/Mf6255KwU.png")

        await purchase_msg.edit(embed=success_embed)

        # Send enhanced DM
        try:
            dm_embed = create_success_embed("🎉 VPS Purchase Complete!", f"Your {plan} VPS is now ready!")
            dm_embed.add_field(name="📊 Purchase Details", 
                value=f"**VPS ID:** #{vps_count}\n**Plan:** {plan} ({processor})\n**Container:** `{container_name}`\n**Cost:** {cost} credits\n**Remaining Credits:** {user_data[user_id]['credits']}", 
                inline=False)
            dm_embed.add_field(name="🚀 Quick Start", 
                value="• Type `.manage` to access your VPS dashboard\n• Click **SSH Access** for terminal\n• Use **Start/Stop** to control power", 
                inline=False)
            await ctx.author.send(embed=dm_embed)
        except discord.Forbidden:
            pass

    except Exception as e:
        # Refund credits on failure
        user_data[user_id]["credits"] += cost
        save_data()
        error_embed = create_error_embed("❌ Purchase Failed", f"Credits refunded. Error: {str(e)}")
        await purchase_msg.edit(embed=error_embed)

@bot.command(name='buyc')
@maintenance_check()
async def buy_credits(ctx):
    """Get payment information for credits"""
    user = ctx.author
    embed = create_embed("💳 Purchase Credits", "Choose your payment method below:", 0x1a1a1a)

    payment_fields = [
        {"name": "🇮🇳 UPI Payment", "value": f"```\n{os.getenv('UPI_ID', '9526303242@fam')}\n```", "inline": False},
        {"name": "💰 PayPal", "value": f"```\n{os.getenv('PAYPAL_EMAIL', 'example@paypal.com')}\n```", "inline": False},
        {"name": "₿ Cryptocurrency", "value": "BTC, ETH, USDT accepted\nContact admin for wallet details", "inline": False},
        {"name": "📋 Purchase Process", 
         "value": "1. **Pay** using any method above\n2. **Contact admin** with transaction proof\n3. **Receive credits** within 24 hours\n4. **Buy VPS** with `.buywc <plan>`", 
         "inline": False},
        {"name": "💡 Credit Rates", 
         "value": "• ₹1 = 1 Credit\n• $1 = 84 Credits\n• Minimum: ₹50 / $1", 
         "inline": False}
    ]

    for field in payment_fields:
        embed.add_field(**field)

    try:
        await user.send(embed=embed)
        await ctx.send(embed=create_success_embed("📧 Payment Info Sent", "Check your DMs for payment details!"))
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ DM Failed", "Enable DMs to receive payment information!"))

@bot.command(name='plans')
@maintenance_check()
async def show_plans(ctx):
    """Show available VPS plans"""
    embed = create_embed("💎 VPS Plans - Heaven node v1", "Choose your perfect VPS plan:", 0x1a1a1a)

    plan_fields = [
        {"name": "🚀 Starter Plan", 
         "value": "**RAM:** 4 GB\n**CPU:** 1 Core\n**Storage:** 10 GB\n**OS:** Ubuntu 22.04\n━━━━━━━━━━━━━━\n💰 **Intel:** ₹42 | **AMD:** ₹83", 
         "inline": False},
        {"name": "⚡ Basic Plan", 
         "value": "**RAM:** 8 GB\n**CPU:** 1 Core\n**Storage:** 10 GB\n**OS:** Ubuntu 22.04\n━━━━━━━━━━━━━━\n💰 **Intel:** ₹96 | **AMD:** ₹164", 
         "inline": False},
        {"name": "🔥 Standard Plan", 
         "value": "**RAM:** 12 GB\n**CPU:** 2 Cores\n**Storage:** 10 GB\n**OS:** Ubuntu 22.04\n━━━━━━━━━━━━━━\n💰 **Intel:** ₹192 | **AMD:** ₹320", 
         "inline": False},
        {"name": "💎 Pro Plan", 
         "value": "**RAM:** 16 GB\n**CPU:** 2 Cores\n**Storage:** 10 GB\n**OS:** Ubuntu 22.04\n━━━━━━━━━━━━━━\n💰 **Intel:** ₹220 | **AMD:** ₹340", 
         "inline": False}
    ]

    for field in plan_fields:
        embed.add_field(**field)

    embed.add_field(name="🛒 How to Purchase", 
        value="1. **Get Credits:** `.buyc` for payment info\n2. **Buy VPS:** `.buywc <plan> <processor>`\n3. **Example:** `.buywc Starter Intel`", 
        inline=False)
    embed.add_field(name="🆓 Free Options", 
        value="Check `.freeplans` for boost and invite plans!", 
        inline=False)
    embed.set_footer(text="All plans include • Full root access • SSH access • Docker support")
    await ctx.send(embed=embed)

# Add essential commands from original
@bot.command(name='list-all')
@is_admin()
@maintenance_check()
async def list_all_vps(ctx):
    """List all VPS and user information (Admin only)"""
    embed = create_embed("📊 All VPS Information", "Complete system overview", 0x1a1a1a)
    
    total_vps = sum(len(vps_list) for vps_list in vps_data.values())
    total_users = len(vps_data)
    running_vps = sum(1 for vps_list in vps_data.values() for vps in vps_list if vps.get('status') == 'running')
    stopped_vps = total_vps - running_vps
    
    system_info = get_system_info()
    if system_info:
        embed.add_field(name="🖥️ System Resources", 
            value=f"**CPU:** {system_info['cpu_usage']:.1f}%\n**Memory:** {system_info['memory_usage']:.1f}% ({system_info['memory_available']}GB free)\n**Disk:** {system_info['disk_usage']:.1f}% ({system_info['disk_free']}GB free)", 
            inline=True)
    
    embed.add_field(name="📈 VPS Statistics", 
        value=f"**Total Users:** {total_users}\n**Total VPS:** {total_vps}\n**Running:** {running_vps}\n**Stopped:** {stopped_vps}", 
        inline=True)
    
    # Recent activity
    vps_info = []
    for user_id, vps_list in list(vps_data.items())[:5]:  # Show first 5 users
        try:
            user = await bot.fetch_user(int(user_id))
            for i, vps in enumerate(vps_list):
                status_emoji = "🟢" if vps.get('status') == 'running' else "🔴"
                vps_info.append(f"{status_emoji} **{user.name}** - `{vps.get('container_name', 'Unknown')}` ({vps.get('plan', 'Custom')})")
        except:
            vps_info.append(f"❓ **Unknown User** ({user_id}) - {len(vps_list)} VPS")
    
    if vps_info:
        embed.add_field(name="🖥️ Recent VPS", value="\n".join(vps_info), inline=False)
    
    if total_users > 5:
        embed.add_field(name="➕ Additional", value=f"... and {total_users - 5} more users with VPS", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='adminc')
@is_admin()
@maintenance_check()
async def admin_credits(ctx, user: discord.Member, amount: int):
    """Add credits to user (Admin only)"""
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Invalid Amount", "Amount must be positive."))
        return
    
    user_id = str(user.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    
    old_balance = user_data[user_id]["credits"]
    user_data[user_id]["credits"] += amount
    new_balance = user_data[user_id]["credits"]
    save_data()
    
    embed = create_success_embed("💰 Credits Added", f"Successfully added credits to {user.mention}")
    embed.add_field(name="Transaction Details", 
        value=f"**Amount Added:** +{amount:,} credits\n**Previous Balance:** {old_balance:,}\n**New Balance:** {new_balance:,}\n**Added by:** {ctx.author.mention}", 
        inline=False)
    await ctx.send(embed=embed)
    
    # Notify user
    try:
        dm_embed = create_success_embed("💰 Credits Received", f"You received {amount:,} credits!")
        dm_embed.add_field(name="Details", value=f"**New Balance:** {new_balance:,} credits\n**From:** {ctx.author.mention}", inline=False)
        await user.send(embed=dm_embed)
    except discord.Forbidden:
        pass

@bot.command(name='adminrc')
@is_admin()
@maintenance_check()
async def admin_remove_credits(ctx, user: discord.Member, amount_or_all: str):
    """Remove credits from user (Admin only)"""
    user_id = str(user.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    
    current_credits = user_data[user_id]["credits"]
    
    if amount_or_all.lower() == "all":
        removed = current_credits
        user_data[user_id]["credits"] = 0
        action = f"All {removed:,} credits removed"
    else:
        try:
            amount = int(amount_or_all)
            if amount <= 0:
                await ctx.send(embed=create_error_embed("Invalid Amount", "Use positive number or 'all'"))
                return
            removed = min(amount, current_credits)
            user_data[user_id]["credits"] -= removed
            action = f"{removed:,} credits removed"
        except ValueError:
            await ctx.send(embed=create_error_embed("Invalid Amount", "Enter number or 'all'"))
            return
    
    save_data()
    
    embed = create_warning_embed("💸 Credits Removed", f"Credits removed from {user.mention}")
    embed.add_field(name="Transaction Details", 
        value=f"**Amount Removed:** -{removed:,} credits\n**Remaining Balance:** {user_data[user_id]['credits']:,}\n**Removed by:** {ctx.author.mention}", 
        inline=False)
    await ctx.send(embed=embed)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN not found in .env file!")
        raise SystemExit("Please set DISCORD_TOKEN in .env file")
    
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")