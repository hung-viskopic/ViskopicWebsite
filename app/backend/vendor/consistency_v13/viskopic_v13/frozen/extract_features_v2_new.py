from __future__ import annotations
import argparse, math, re
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

TRANSITIONS = ["however","therefore","moreover","besides","furthermore","in conclusion",
               "on the other hand","on the one hand","for example","for instance",
               "in addition","as a result","firstly","secondly","finally","despite","although","while"]
FUNCTION_WORDS = ["a","an","the","and","but","or","so","because","although","while","of","to","in","on",
                  "for","with","at","from","by","is","are","was","were","be","been","being","this","that",
                  "these","those","it","they","we","he","she","you","i","their","our","his","her","its",
                  "can","could","may","might","must","should","would","will","not","no","more","most",
                  "some","many","much"]
FIRST_PERSON={"i","me","my","mine","we","us","our","ours"}
SECOND_PERSON={"you","your","yours"}
THIRD_PERSON={"he","him","his","she","her","hers","they","them","their","theirs","it","its"}
CONTRACTIONS={"don't","doesn't","didn't","can't","couldn't","won't","wouldn't","shouldn't","isn't",
              "aren't","wasn't","weren't","it's","that's","there's","i'm","we're","they're","you're",
              "i've","we've","they've","you've","i'll","we'll","they'll","you'll"}

def safe(a,b): return a/b if b else 0.0
def clean(v): return 0.0 if math.isnan(v) or math.isinf(v) else round(float(v),4)
def get_words(t): return re.findall(r"\b[\w']+\b", t.lower())
def get_sentences(t): return [x.strip() for x in re.split(r"(?<=[.!?])\s+", t.strip()) if x.strip()]
def get_paragraphs(t):
    p=[x.strip() for x in re.split(r"\n\s*\n",t) if x.strip()]
    if len(p)==1:
        lines=[x.strip() for x in t.splitlines() if x.strip()]
        if len(lines)>1: p=lines
    return p
def syllables(w):
    w=re.sub(r"[^a-z]","",w.lower())
    if not w: return 0
    c=len(re.findall(r"[aeiouy]+",w))
    if w.endswith("e") and not w.endswith(("le","ye")) and c>1: c-=1
    return max(1,c)

def extract_features(text):
    ws=get_words(text); ss=get_sentences(text); ps=get_paragraphs(text)
    wc=Counter(ws); wl=[len(w) for w in ws]; sl=[len(get_words(s)) for s in ss]; pl=[len(get_words(p)) for p in ps]
    n=len(ws); ns=len(ss); npg=len(ps); uniq=len(wc)
    syl=[syllables(w) for w in ws]; syl_total=sum(syl); complex_words=sum(x>=3 for x in syl)
    hapax=sum(c==1 for c in wc.values()); dis=sum(c==2 for c in wc.values()); punct=Counter(text); lower=text.lower()
    f={
      "word_count":n,"sentence_count":ns,"paragraph_count":npg,"character_count":len(text),
      "character_count_no_spaces":len(re.sub(r"\s+","",text)),
      "avg_sentence_length":safe(n,ns),
      "sentence_length_std":float(np.std(sl,ddof=1)) if len(sl)>1 else 0.0,
      "sentence_length_min":min(sl,default=0),"sentence_length_max":max(sl,default=0),
      "avg_paragraph_length":safe(n,npg),
      "paragraph_length_std":float(np.std(pl,ddof=1)) if len(pl)>1 else 0.0,
      "avg_word_length":safe(sum(wl),n),
      "word_length_std":float(np.std(wl,ddof=1)) if len(wl)>1 else 0.0,
      "unique_word_count":uniq,"type_token_ratio":safe(uniq,n),
      "root_type_token_ratio":safe(uniq,math.sqrt(n) if n else 0),
      "corrected_type_token_ratio":safe(uniq,math.sqrt(2*n) if n else 0),
      "hapax_ratio":safe(hapax,n),"hapax_unique_ratio":safe(hapax,uniq),
      "dislegomena_ratio":safe(dis,n),
      "repeated_word_ratio":safe(sum(c for c in wc.values() if c>1),n),
      "avg_syllables_per_word":safe(syl_total,n),"complex_word_ratio":safe(complex_words,n),
      "flesch_reading_ease":206.835-1.015*safe(n,ns)-84.6*safe(syl_total,n),
      "flesch_kincaid_grade":0.39*safe(n,ns)+11.8*safe(syl_total,n)-15.59,
      "gunning_fog_index":0.4*(safe(n,ns)+100*safe(complex_words,n)),
      "commas_per_100_words":100*safe(punct[","],n),
      "semicolons_per_100_words":100*safe(punct[";"],n),
      "colons_per_100_words":100*safe(punct[":"],n),
      "question_marks_per_100_words":100*safe(punct["?"],n),
      "exclamation_marks_per_100_words":100*safe(punct["!"],n),
      "parentheses_per_100_words":100*safe(punct["("]+punct[")"],n),
      "hyphens_per_100_words":100*safe(punct["-"],n),
      "first_person_per_100_words":100*safe(sum(wc[w] for w in FIRST_PERSON),n),
      "second_person_per_100_words":100*safe(sum(wc[w] for w in SECOND_PERSON),n),
      "third_person_per_100_words":100*safe(sum(wc[w] for w in THIRD_PERSON),n),
      "contractions_per_100_words":100*safe(sum(wc[w] for w in CONTRACTIONS),n),
      "long_sentences_ratio":safe(sum(x>=25 for x in sl),ns),
      "short_sentences_ratio":safe(sum(x<=10 for x in sl),ns),
      "long_words_ratio":safe(sum(x>=7 for x in wl),n),
      "very_long_words_ratio":safe(sum(x>=10 for x in wl),n),
    }
    for w in FUNCTION_WORDS: f[f"function_{w}_per_100_words"]=100*safe(wc[w],n)
    total=0
    for t in TRANSITIONS:
        c=lower.count(t); total+=c
        f[f"transition_{t.replace(' ','_')}_per_100_words"]=100*safe(c,n)
    f["transition_total_per_100_words"]=100*safe(total,n)
    f["transition_total_per_sentence"]=safe(total,ns)
    openings=[]
    for s in ss:
        sw=get_words(s)
        if sw: openings.append(sw[0])
    oc=Counter(openings)
    f["unique_sentence_opening_ratio"]=safe(len(oc),len(openings))
    f["repeated_sentence_opening_ratio"]=safe(sum(c for c in oc.values() if c>1),len(openings))
    for o in ["however","therefore","moreover","firstly","secondly","finally","in","on","the","this","it","there"]:
        f[f"sentence_opening_{o}_ratio"]=safe(oc[o],len(openings))
    return {k:clean(v) for k,v in f.items()}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--folder",default="data/human_essays")
    p.add_argument("--output",default="writing_profile_v2.csv")
    a=p.parse_args()
    folder=Path(a.folder); out=Path(a.output)
    if not folder.exists(): raise FileNotFoundError(f"Folder not found: {folder.resolve()}")
    files=sorted(folder.glob("*.txt"))
    if not files: raise ValueError(f"No .txt files found in: {folder.resolve()}")
    rows=[]
    for file in files:
        text=file.read_text(encoding="utf-8").strip()
        if len(text)<100: continue
        row={"essay":file.name}; row.update(extract_features(text)); rows.append(row)
    df=pd.DataFrame(rows); df.to_csv(out,index=False)
    print(f"Processed essays: {len(df)}")
    print(f"Extracted features: {len(df.columns)-1}")
    print(f"Saved profile to: {out.resolve()}")
    preview=["essay","word_count","avg_sentence_length","sentence_length_std","avg_word_length",
             "type_token_ratio","hapax_ratio","commas_per_100_words",
             "transition_total_per_100_words","flesch_reading_ease"]
    print(df[preview].to_string(index=False))

if __name__=="__main__":
    main()
