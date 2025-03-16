# First Modal that is called ->Asking him his ingame name, then it will call the TraineeDropdownView

class TraineeDropdownView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.add_item(RegionSelect())
        self.add_item(RecruiterSelect())


class TraineeRoleModal(discord.ui.Modal, title="Request Trainee Role"):
    ingame_name = discord.ui.TextInput(label="In-Game Name", placeholder="Enter your in-game name")
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id_str = str(interaction.user.id)
            pending_requests[user_id_str] = {
                "request_type": "trainee_role",
                "ingame_name": self.ingame_name.value
            }
            save_requests()
            view = TraineeDropdownView(user_id=interaction.user.id)
            await interaction.response.send_message(
                "Please select your **Region** and **Recruiter** below:",
                view=view,
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error submitting trainee role modal: {e}", ephemeral=True)
            
            
# Define finalize_trainee_request as a module-level function.
async def finalize_trainee_request(interaction: discord.Interaction, user_id_str: str):
    try:
        request = pending_requests.get(user_id_str)
        if not request:
            await interaction.followup.send("❌ No pending request found to finalize.", ephemeral=True)
            return
        region = request.get("region")
        recruiter_name = request.get("selected_recruiter_name")
        recruiter_id = request.get("selected_recruiter_id")
        if not region or not recruiter_name or not recruiter_id:
            await interaction.followup.send("❌ Please complete all selections.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
        if not guild:
            await interaction.followup.send("❌ Guild not found.", ephemeral=True)
            return
        channel = guild.get_channel(REQUESTS_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("❌ Requests channel not found.", ephemeral=True)
            return
        embed = discord.Embed(
            title="New Trainee Role Request:",
            description=f"User <@{interaction.user.id}> has requested a trainee role!",
            color=0x0080c0
        )
        embed.add_field(name="In-Game Name:", value=f"```{request['ingame_name']}```", inline=True)
        embed.add_field(name="Accepted By:", value=f"```{recruiter_name}```", inline=True)
        embed.add_field(name="Region:", value=f"```{region}```", inline=True)
        view = RequestActionView(
            user_id=interaction.user.id,
            request_type="trainee_role",
            ingame_name=request['ingame_name'],
            region=region,
            recruiter=recruiter_name
        )
        await channel.send(f"<@{recruiter_id}>")
        await channel.send(embed=embed, view=view)
        await interaction.followup.send("✅ Your trainee role request has been submitted! Please allow us some time to accept this request.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error finalizing trainee request: {e}", ephemeral=True)