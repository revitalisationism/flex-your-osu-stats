-- Place as a LocalScript in StarterPlayer > StarterPlayerScripts.
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local player = Players.LocalPlayer
local gui = Instance.new("ScreenGui")
gui.Name = "OsuLinkStatus"
gui.ResetOnSpawn = false
gui.Parent = player:WaitForChild("PlayerGui")
local panel = Instance.new("TextLabel")
panel.Size = UDim2.new(0.8, 0, 0, 120)
panel.Position = UDim2.new(0.1, 0, 0.7, 0)
panel.BackgroundColor3 = Color3.fromRGB(28, 24, 35)
panel.TextColor3 = Color3.fromRGB(255, 255, 255)
panel.TextSize = 18
panel.TextWrapped = true
panel.RichText = false
panel.Visible = false
panel.Parent = gui
local generation = 0
ReplicatedStorage:WaitForChild("OsuLinkNotice").OnClientEvent:Connect(function(message)
    generation += 1
    local current = generation
    panel.Text = message
    panel.Visible = true
    task.delay(60, function()
        if current == generation then panel.Visible = false end
    end)
end)
