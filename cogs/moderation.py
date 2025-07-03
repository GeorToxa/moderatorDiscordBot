import asyncio

import aiosqlite
import discord

from datetime import timedelta, datetime

from discord.ext import commands

from database import Database

class ModeratingCog(commands.Cog):
    def __init__(self, bot: commands.Bot, log_channel_id: int = None):
        self.bot = bot
        self.log_channel_id = log_channel_id
        self.loop = asyncio.get_event_loop()
        self.db = Database(self, self.loop)
        self.PUNISHMENTS = {           # total warnings ➜ (action, duration)
            3: ("mute", timedelta(hours=1)),
            4: ("mute", timedelta(hours=12)),
            5: ("mute", timedelta(hours=24)),
            6: ("mute",  timedelta(hours=168)),
            7: ("mute",  timedelta(hours=672)),
            8: ("ban",  None),  # None = permanent
        }

    def __repr__(self):
        return f"{self.__cog_name__}"

    async def send_log(self, guild: discord.Guild, message: str):
        """
        Sends a log message to the configured log channel in the specified guild.

        :param guild: The Discord guild (server) where the log should be sent.
        :param message: The message content to send to the log channel.
        :return: None
        """
        if not self.log_channel_id:
            return

        channel = guild.get_channel(self.log_channel_id)

        if channel:
            await channel.send(message)

    async def cog_load(self):
        """
        Loads all active punishments from the database and schedules unpunishment tasks for them.

        This method is called when the cog is loaded. It retrieves all active punishments,
        calculates the remaining time for each, and schedules a task to automatically
        unmute or unban the user when the punishment expires.
        """
        await self.db.connect()
        rows = await self.db.get_all_active_punishments()

        for user_id, guild_id, action, ends_at_str in rows:
            ends_at = datetime.fromisoformat(ends_at_str)
            now = datetime.utcnow()
            delay = (ends_at - now).total_seconds()
            if delay <= 0:
                delay = 0

            self.bot.loop.create_task(self._delayed_unpunish(user_id, guild_id, action, delay))

    async def _delayed_unpunish(self, user_id, guild_id, action, delay):
        """
        Waits for the specified delay, then removes the punishment (mute or ban) from the user.

        :param user_id: The ID of the user to unpunish.
        :param guild_id: The ID of the guild where the punishment was applied.
        :param action: The type of punishment ("mute" or "ban").
        :param delay: The number of seconds to wait before unpunishing.
        :return: None
        """
        await asyncio.sleep(delay)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        member = guild.get_member(user_id)
        if not member:
            return

        if action == "mute":
            mute_role = discord.utils.get(guild.roles, name="Muted")
            if mute_role and mute_role in member.roles:
                await member.remove_roles(mute_role)
                channel = discord.utils.get(guild.text_channels, name="general")  # или лог-канал
                if channel:
                    await channel.send(f"🔊 {member.mention} was automatically unmuted.")
        elif action == "ban":
            try:
                await guild.unban(discord.Object(id=user_id))
                channel = discord.utils.get(guild.text_channels, name="general")  # или лог-канал
                if channel:
                    await channel.send(f"🔓 User with ID {user_id} was automatically unbanned.")
            except discord.NotFound:
                pass

        await self.db.delete_punishment(user_id, guild_id, action)

    async def apply_punishment(self, member: discord.Member, warnings_count: int, ctx):
        """
        Applies the appropriate punishment to a member based on their total warnings.

        :param member: The Discord member to punish.
        :param warnings_count: The total number of warnings the member has.
        :param ctx: The command context.
        :return: None
        """
        if warnings_count in self.PUNISHMENTS:
            action, duration = self.PUNISHMENTS[warnings_count]

            if action == "mute":
                mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
                if not mute_role:
                    mute_role = await ctx.guild.create_role(name="Muted")
                    for channel in ctx.guild.channels:
                        await channel.set_permissions(mute_role, speak=False, send_messages=False)

                if mute_role in member.roles:
                    await member.timeout(None)

                await member.add_roles(mute_role)
                await ctx.send(f"🔇 {member.mention} has been muted for {duration}.")

                if duration:
                    async def unmute_later():
                        await asyncio.sleep(duration.total_seconds())
                        await member.remove_roles(mute_role)
                        await ctx.send(f"🔊 {member.mention} has been unmuted.")

                    ctx.bot.loop.create_task(unmute_later())

            elif action == "ban":
                bans = [entry async for entry in ctx.guild.bans()]
                if discord.utils.get(bans, user=member):
                    await ctx.send(f"⚠ {member.mention} is already banned.")
                    return

                await member.ban(reason=f"Reached {warnings_count} warnings.")
                await ctx.send(
                    f"⛔ {member.mention} has been banned ({'permanently' if duration is None else f'for {duration}'}).")

                if duration:
                    async def unban_later():
                        await asyncio.sleep(duration.total_seconds())
                        await ctx.guild.unban(discord.Object(id=member.id))
                        await ctx.send(f"🔓 {member.mention} has been unbanned after temporary ban.")

                    ctx.bot.loop.create_task(unban_later())

    async def adjust_punishment_after_change(self, member: discord.Member, warn_count: int, ctx):
        """
        Adjusts a member's punishment after their warning count changes.

        :param member: The Discord member whose punishment may need adjustment.
        :param warn_count: The current number of warnings for the member.
        :param ctx: The command context.
        :return: None
        """
        if warn_count < 3:
            mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
            if mute_role and mute_role in member.roles:
                await member.remove_roles(mute_role)
                await ctx.send(f"🔊 {member.mention} has been unmuted (warnings below threshold).")

            try:
                await member.timeout(None)
            except discord.Forbidden:
                pass

    @commands.command(name="purge")
    @commands.has_permissions(administrator=True)
    async def cmd_purge(self, ctx: commands.Context, amount: int):
        """
        Deletes a specified number of messages from the current channel.

        :param ctx: The command context.
        :param amount: The number of messages to delete (1-100).
        :return: None
        """
        if amount <= 0 or amount > 100:
            await ctx.send("⚠ Write a number 0 to 100.")
            return
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.send(f"🧹 Removed {len(deleted) - 1} messages.", delete_after=5)
        await self.send_log(ctx.guild, f"⚠ {ctx.author} has removed {len(deleted) - 1} messages.")

    @commands.command(name="mute")
    @commands.has_permissions(administrator=True)
    async def cmd_mute(self, ctx: commands.Context, member: discord.Member, duration: int = 10):
        """
        Mutes a member for a specified duration in minutes.

        :param ctx: The command context.
        :param member: The Discord member to mute.
        :param duration: Duration of the mute in minutes (default is 10).
        :return: None
        """
        try:
            await member.timeout(timedelta(minutes=duration), reason=f"Muted by {ctx.author}")
            await ctx.send(f"🔇 {member.mention} was muted for {duration} minutes.")
            await self.send_log(ctx.guild, f"⚠ {member} was muted by {ctx.author} for {duration} minutes.")
        except discord.Forbidden:
            await ctx.send("❌ I can't mute him.")

    @commands.command(name="unmute")
    @commands.has_permissions(administrator=True)
    async def cmd_unmute(self, ctx: commands.Context, member: discord.Member):
        """
        Unmutes a specified member in the guild.

        :param ctx: The command context.
        :param member: The Discord member to unmute.
        :return: None
        """
        try:
            await member.timeout(None)
            await ctx.send(f"🔊 {member.mention} was unmuted.")
            await self.send_log(ctx.guild, f"⚠ {member} was unmuted by {ctx.author}.")
        except discord.Forbidden:
            await ctx.send("❌ I can't unmute him.")

    @commands.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    async def cmd_warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        """
        Issues a warning to a member and applies punishment if thresholds are reached.

        :param ctx: The command context.
        :param member: The Discord member to warn.
        :param reason: The reason for the warning.
        :return: None
        """
        await self.db.add_warning(member.id, ctx.guild.id, ctx.author.id, reason, datetime.utcnow().isoformat())

        total_warnings = (await self.db.get_warnings_count(member.id, ctx.guild.id))
        await ctx.send(f"⚠️ {member.mention} warned ({total_warnings} total). Reason: {reason}")
        try:
            await member.send(f"⚠️ You were warned in {ctx.guild.name}: {reason} (Warnings: {total_warnings})")
            await self.send_log(ctx.guild, f"⚠ {member} was warned by {ctx.author}. Reason: {reason}.")
        except discord.Forbidden:
            pass

        if total_warnings in self.PUNISHMENTS:
            action, duration = self.PUNISHMENTS[total_warnings]
            await self.apply_punishment(member, total_warnings, ctx)

    @commands.command(name="warns", aliases=["warnlist"])
    @commands.has_permissions(manage_messages=True)
    async def cmd_warns(self, ctx: commands.Context, member: discord.Member):
        """
        Displays all warnings for a specified member.

        :param ctx: The command context.
        :param member: The Discord member whose warnings will be shown.
        :return: None
        """
        rows = await self.db.get_all_user_warnings(member.id, ctx.guild.id)

        if not rows:
            await ctx.send(f"✅ {member.mention} has no warnings.")
            return

        embed = discord.Embed(title=f"⚠ Warnings for {member}", color=discord.Color.orange())
        for i, (reason, timestamp, moderator_id) in enumerate(rows, start=1):
            mod = ctx.guild.get_member(moderator_id)
            mod_name = mod.name if mod else f"ID {moderator_id}"
            embed.add_field(name=f"{i}. {timestamp[:19]}", value=f"Moderator: {mod_name}\nReason: {reason}",
                            inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="delwarn")
    @commands.has_permissions(manage_messages=True)
    async def cmd_delwarn(self, ctx: commands.Context, member: discord.Member):
        """
        Deletes the latest warning for a specified member.

        :param ctx: The command context.
        :param member: The Discord member whose latest warning will be deleted.
        :return: None
        """
        row = await self.db.get_one_user_warning(member.id, ctx.guild.id)
        if not row:
            await ctx.send(f"❌ {member.mention} has no warnings.")
            return

        await self.db.delete_last_warning(row[0])
        await ctx.send(f"✅ Removed the latest warning for {member.mention}.")
        await self.send_log(ctx.guild, f"⚠ {ctx.author} has removed warning for {member}.")

        warn_count = (await self.db.get_warnings_count(member.id, ctx.guild.id))

        await self.apply_punishment(member, warn_count, ctx)
        await self.adjust_punishment_after_change(member, warn_count, ctx)

    @commands.command(name="clearwarns")
    @commands.has_permissions(administrator=True)
    async def cmd_clearwarns(self, ctx: commands.Context, member: discord.Member):
        """
        Clears all warnings for a specified member, unmutes and unbans them if necessary.

        :param ctx: The command context.
        :param member: The Discord member whose warnings will be cleared.
        :return: None
        """
        await self.db.delete_all_user_warnings(member.id, ctx.guild.id)
        await ctx.send(f"🧹 All warnings for {member.mention} have been cleared.")
        await self.send_log(ctx.guild, f"⚠ {ctx.author} has cleared all warnings for {member}.")

        try:
            await member.timeout(None)
        except discord.Forbidden:
            pass

        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if mute_role and mute_role in member.roles:
            try:
                await member.remove_roles(mute_role)
                await ctx.send(f"🔊 {member.mention} has been unmuted.")
            except discord.Forbidden:
                await ctx.send(f"⚠ Не могу убрать мут у {member.mention} (нет прав).")

        try:
            await ctx.guild.unban(discord.Object(id=member.id))
        except discord.NotFound:
            pass

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def cmd_kick(self, ctx: commands.Context, member: discord.Member, *, reason="With out reason"):
        """
        Kicks a specified member from the guild.

        :param ctx: The command context.
        :param member: The Discord member to kick.
        :param reason: The reason for kicking the member.
        :return: None
        """
        try:
            await member.kick(reason=reason)
            await ctx.send(f"🔨 {member.mention} was kicked. Reason: {reason}.")
            await self.send_log(ctx.guild, f"⚠ {member} was kicked by {ctx.author}. Reason: {reason}.")
        except discord.Forbidden:
            await ctx.send("❌ I can't kick him.")

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def cmd_ban(self, ctx: commands.Context, member: discord.Member, *, reason="With out reason"):
        """
        Bans a specified member from the guild.

        :param ctx: The command context.
        :param member: The Discord member to ban.
        :param reason: The reason for banning the member.
        :return: None
        """
        try:
            await member.ban(reason=reason)
            await ctx.send(f"🔨 {member.mention} was banned. Reason: {reason}.")
            await self.send_log(ctx.guild, f"⚠ {member} was banned by {ctx.author}. Reason: {reason}.")
        except discord.Forbidden:
            await ctx.send("❌ I can't ban him.")

    @commands.command(name="unban")
    @commands.has_permissions(administrator=True)
    async def cmd_unban(self, ctx: commands.Context, *, user_input: str):
        """
        Unbans a user by their ID or username.

        :param ctx: The command context.
        :param user_input: The user ID, mention, or username to unban.
        :return: None
        """
        bans = [entry async for entry in ctx.guild.bans()]
        try:
            user_id = int(user_input.strip("<@!>"))
            for ban_entry in bans:
                if ban_entry.user.id == user_id:
                    await ctx.guild.unban(ban_entry.user)
                    await ctx.send(f"✅ Unbanned user `{ban_entry.user}`.")
                    await self.send_log(ctx.guild, f"⚠ {ban_entry.user} was unbanned by {ctx.author}.")
                    return
        except ValueError:
            pass

        for ban_entry in bans:
            if ban_entry.user.name.lower() == user_input.lower():
                await ctx.guild.unban(ban_entry.user)
                await ctx.send(f"✅ Unbanned user `{ban_entry.user}`.")
                await self.send_log(ctx.guild, f"⚠ {ban_entry.user} was unbanned by {ctx.author}.")
                return

        await ctx.send(f"⚠ No banned user found with name or ID `{user_input}`.")

    @commands.command(name="banlist", aliases=["bans", "banned"])
    @commands.has_permissions(administrator=True)
    async def cmd_banlist(self, ctx: commands.Context):
        """
        Displays a paginated list of all banned users in the guild.

        :param ctx: The command context.
        :return: None
        """
        bans = [entry async for entry in ctx.guild.bans()]
        if not bans:
            return await ctx.send("✅ No users are currently banned.")

        pages = [bans[i:i + 5] for i in range(0, len(bans), 5)]
        index = 0
        embed = self.build_banlist_embed(pages, index)
        message = await ctx.send(embed=embed)

        for emoji in ["⬅️", "➡️"]:
            await message.add_reaction(emoji)

        def check(reaction, user):
            return (
                    user == ctx.author and
                    reaction.message.id == message.id and
                    str(reaction.emoji) in ["⬅️", "➡️"]
            )

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)

                if str(reaction.emoji) == "➡️" and index < len(pages) - 1:
                    index += 1
                elif str(reaction.emoji) == "⬅️" and index > 0:
                    index -= 1
                else:
                    await message.remove_reaction(reaction, user)
                    continue

                embed = self.build_banlist_embed(pages, index)
                await message.edit(embed=embed)
                await message.remove_reaction(reaction, user)

            except asyncio.TimeoutError:
                break

    def build_banlist_embed(self, pages, index):
        """
        Builds a Discord embed for displaying a paginated list of banned users.

        :param pages: List of pages, each containing up to 5 ban entries.
        :param index: The current page index to display.
        :return: A discord.Embed object representing the ban list page.
        """
        embed = discord.Embed(
            title="🚫 Banned Users",
            description=f"Page {index + 1}/{len(pages)}",
            color=discord.Color.red()
        )
        for i, entry in enumerate(pages[index], start=1 + index * 5):
            user = entry.user
            reason = entry.reason or "No reason provided"
            embed.add_field(
                name=f"{i}. {user} ({user.id})",
                value=f"Reason: {reason}",
                inline=False
            )
        return embed

async def setup(bot: commands.Bot):
    await bot.add_cog(ModeratingCog(bot))