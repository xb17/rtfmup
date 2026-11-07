var app = WebApplication.CreateBuilder(args).Build();
app.MapGet("/", () => "ok");
app.Run("http://localhost:5000");
