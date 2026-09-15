import boto3, os, json

current_dir = os.path.abspath(os.path.dirname(__file__))
repo_root = os.path.abspath(os.path.join(current_dir, ".."))
env_path = os.path.join(repo_root, '.env')

if os.path.exists(env_path):
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

session = boto3.Session(
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    region_name=os.environ.get('AWS_REGION', 'ap-south-1')
)
t = session.resource('dynamodb').Table(os.environ.get('DYNAMODB_TABLE_NAME', 'InDiiServe_Asha_Healthcare_Transcripts_NEW'))
res = t.scan()['Items']
res.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
if res:
    print('Transcript entries of first item:')
    for entry in res[0]['transcript']:
        print(json.dumps(entry, indent=2, default=str))
else:
    print('Empty table')
