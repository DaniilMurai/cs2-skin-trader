import re
w=open('/private/tmp/claude-501/-Users-daniilmurai--claude-spares-spare-EGwwSp/82e44451-4f47-4269-a3f4-2c29f8a2e988/scratchpad/wp.wiki').read()
rows=re.findall(r'! scope="row" \| \[\[[^\]]*\|([^\]]*)\]\].*?\n(?:\|[^\n]*\n)*?\| \{\{dts\|([^}]*)\}\}([^\n|]*)',w)
print(len(rows))
for r in rows: print(r)
