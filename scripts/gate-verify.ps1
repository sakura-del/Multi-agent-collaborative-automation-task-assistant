$ErrorActionPreference = "Stop"
$base = "http://localhost/api/v1"
$stamp = Get-Date -Format "yyMMddHHmmss"
$ua = "gate-a-$stamp"; $ub = "gate-b-$stamp"

function PostJson($url, $token, $obj) {
  $h = @{}; if ($token) { $h["Authorization"] = "Bearer $token" }
  $body = $obj | ConvertTo-Json -Compress -Depth 6
  Invoke-RestMethod -Method Post -Uri $url -Headers $h -ContentType "application/json" -Body $body -TimeoutSec 60
}
function GetR($url, $token) {
  $h = @{}; if ($token) { $h["Authorization"] = "Bearer $token" }
  try { Invoke-RestMethod -Method Get -Uri $url -Headers $h -TimeoutSec 60 }
  catch { [pscustomobject]@{ status = [int]$_.Exception.Response.StatusCode; body = $_.Exception.Message } }
}
function DelR($url, $token) {
  $h = @{}; if ($token) { $h["Authorization"] = "Bearer $token" }
  try { Invoke-RestMethod -Method Delete -Uri $url -Headers $h -TimeoutSec 60 }
  catch { [pscustomobject]@{ status = [int]$_.Exception.Response.StatusCode; body = $_.Exception.Message } }
}

PostJson "$base/auth/register" $null @{username=$ua; email="$ua@test.com"; password="password123"} | Out-Null
PostJson "$base/auth/register" $null @{username=$ub; email="$ub@test.com"; password="password123"} | Out-Null
$ta = (PostJson "$base/auth/login" $null @{account=$ua; password="password123"}).access_token
$tb = (PostJson "$base/auth/login" $null @{account=$ub; password="password123"}).access_token
"tokens: A={0} B={1}" -f [bool]$ta, [bool]$tb

# A uploads a document (multipart). Distinctive ASCII term 'quantumflux' for reliable BM25 hit.
$line = "quantumflux vector search and reranking improve retrieval augmented generation."
$text = (($line + "`n") * 20)
$tmp = Join-Path $env:TEMP "gate-a-$stamp.txt"
[System.IO.File]::WriteAllText($tmp, $text, (New-Object System.Text.UTF8Encoding($false)))

$src = curl.exe -s -X POST "$base/knowledge/documents" -H "Authorization: Bearer $ta" -F "file=@$tmp;type=text/plain" -F "title=GateDocA"
$uploadA = $src | ConvertFrom-Json
$docA = $uploadA.id
"A uploaded doc status={0} chunks={1}" -f $uploadA.index_status, $uploadA.chunk_count

$listB = GetR "$base/knowledge/documents" $tb
"1) B list count (expect 0): {0}" -f $listB.documents.Count

$delB = DelR "$base/knowledge/documents/$docA" $tb
$delBCode = if ($delB.status) { $delB.status } else { 200 }
"2) B delete A doc status (expect 404): {0}" -f $delBCode

$searchB = PostJson "$base/knowledge/search" $tb @{query="quantumflux"; top_k=10}
"3) B search count (expect 0): {0}" -f $searchB.total

$searchA = PostJson "$base/knowledge/search" $ta @{query="quantumflux"; top_k=10}
$foreign = $searchA.results | Where-Object { $_.doc_id -ne $docA }
"4) A search count (expect >=1): {0}, foreign docs (expect 0): {1}" -f $searchA.total, ($foreign | Measure-Object).Count

$delA = DelR "$base/knowledge/documents/$docA" $ta
$delACode = if ($delA.status) { $delA.status } else { 200 }
$listA = GetR "$base/knowledge/documents" $ta
"5) A delete own status (expect 200): {0}; A list after (expect 0): {1}" -f $delACode, $listA.documents.Count

Remove-Item $tmp -ErrorAction SilentlyContinue
"--- gate verification done ---"
