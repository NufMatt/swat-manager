
### Write Somthing in the activity channel
activity_channel = guild.get_channel(ACTIVITY_CHANNEL_ID)
if activity_channel:
    await activity_channel.send(f"❌ {ign} tried to apply for a closed region: {region}")
    embed = discord.Embed(title=f"{interaction.user.name}] tried to apply for REGION!", description=f"{interaction.user.mention} tried to apply for a closed region!", colour=0xcfd746)
