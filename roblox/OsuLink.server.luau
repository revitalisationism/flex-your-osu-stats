-- Place in ServerScriptService. Set the HTTPS backend URL below.
local API = "https://your-backend.example.com"
local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TextChatService = game:GetService("TextChatService")

local notice = Instance.new("RemoteEvent")
notice.Name = "OsuLinkNotice"
notice.Parent = ReplicatedStorage
local busy = {}
local cooldown = {}

local function request(player, action, body)
    local options = {
        Url = API .. "/game/" .. tostring(player.UserId) .. "/" .. action,
        Method = if body then "POST" else "GET",
        Headers = {
            ["Authorization"] = HttpService:GetSecret("OSU_LINK_API_KEY"):AddPrefix("Bearer "),
            ["Content-Type"] = "application/json",
        },
    }
    if body then options.Body = HttpService:JSONEncode(body) end
    local response = HttpService:RequestAsync(options)
    if not response.Success then
        -- Do not expose upstream infrastructure errors or secrets to players.
        if response.StatusCode == 409 then error("Account already linked. Unlink first.", 0) end
        if response.StatusCode == 400 then error("No matching verified request. Check your osu! ID or restart /osulink.", 0) end
        error("Link service unavailable. Please try again shortly.", 0)
    end
    return HttpService:JSONDecode(response.Body)
end

local function refresh(player)
    local data = request(player, "stats")
    if not player.Parent then return end
    player:SetAttribute("OsuLinked", data.linked)
    player:SetAttribute("OsuUserId", if data.linked then data.osu_id else nil)
    -- Numeric attributes are ready for your game's stat cards or overhead display.
    player:SetAttribute("OsuPP", if data.linked then data.pp else nil)
    player:SetAttribute("OsuGlobalRank", if data.linked then data.global_rank else nil)
    player:SetAttribute("OsuCountryRank", if data.linked then data.country_rank else nil)
    player:SetAttribute("OsuAccuracy", if data.linked then data.accuracy else nil)
    player:SetAttribute("OsuPlayCount", if data.linked then data.play_count else nil)
end

local function command(alias, callback)
    local item = Instance.new("TextChatCommand")
    item.Name = alias:sub(2)
    item.PrimaryAlias = alias
    item.Parent = TextChatService
    item.Triggered:Connect(function(source, text)
        if not source then return end
        local player = Players:GetPlayerByUserId(source.UserId)
        if not player or busy[player] or (cooldown[player] or 0) > os.clock() then return end
        busy[player] = true
        cooldown[player] = os.clock() + 5
        local ok, message = pcall(callback, player, text)
        busy[player] = nil
        if player.Parent then
            notice:FireClient(player, if ok then message else "Request failed. Check the account ID and try again; the service may be offline.")
        end
    end)
end

command("/osulink", function(player)
    return request(player, "challenge", {}).message
end)
command("/osuapprove", function(player, text)
    local osuId = text:match("^%S+%s+(%d+)%s*$")
    if not osuId then return "Usage: /osuapprove YOUR_OSU_ID. Approve only your own account." end
    local result = request(player, "approve", { osu_id = osuId })
    pcall(refresh, player)
    return result.message
end)
command("/osuunlink", function(player)
    local result = request(player, "unlink", {})
    pcall(refresh, player)
    return result.message
end)
command("/osustats", function(player)
    refresh(player)
    if not player:GetAttribute("OsuLinked") then return "No account linked. Start with /osulink." end
    return string.format("osu! | PP: %s | Global rank: %s | Accuracy: %s%%",
        tostring(player:GetAttribute("OsuPP") or "Unranked"),
        tostring(player:GetAttribute("OsuGlobalRank") or "Unranked"),
        tostring(player:GetAttribute("OsuAccuracy") or "N/A"))
end)

local function joined(player)
    task.spawn(function()
        while player.Parent do
            pcall(refresh, player)
            task.wait(300)
        end
    end)
end
Players.PlayerAdded:Connect(joined)
for _, player in Players:GetPlayers() do joined(player) end
Players.PlayerRemoving:Connect(function(player)
    cooldown[player] = nil
    busy[player] = nil
end)
