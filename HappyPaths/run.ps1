param(
    [string]$Name = "run",
    [int]$Users = 50,
    [int]$Rate = 5,
    [string]$Time = "2m"
)
locust --headless -u $Users -r $Rate -t $Time --csv "../Results/$Name" --html "../Results/$Name.html" --logfile "../Results/$Name.log"
python ..\Results\Graphs\graphBuilder.py "../Results/$Name"
python ..\Results\Graphs\milestones.py "../Results/$Name"