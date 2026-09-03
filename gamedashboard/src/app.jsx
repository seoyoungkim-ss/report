const { useState, useEffect, useMemo, useRef, useCallback, useLayoutEffect } = React;

/* ============================== constants ============================== */
const PALETTE = ["#ff7a29","#26e0d9","#ffd166","#ff5c8a","#7ee787","#5aa9ff","#c792ea","#ff9f45","#4dd0e1"];
const DAYS = [
  { key:"day1", label:"DAY 1", date:"9/7(월)", game:"자이언트 젠가", place:"본관동 로비", unit:"3인 1팀", format:"bracket", hasFirstMover:true },
  { key:"day2", label:"DAY 2", date:"9/8(화)", game:"모바일 카트라이더", place:"본관동 대강당", unit:"4vs4 팀전 · 3판 2선승", format:"bracket", hasSetScore:true },
  { key:"day3", label:"DAY 3", date:"9/9(수)", game:"팀빌딩 게임", place:"잔디광장", unit:"6인 1팀", format:"ranking" },
  { key:"day4", label:"DAY 4", date:"9/10(목)", game:"실내 컬링", place:"본관동 로비", unit:"4인 1팀", format:"bracket" },
];
const dayInfo = (k) => DAYS.find(d=>d.key===k);
const STORAGE_KEY = "summer-escape-state-v1";
const EXEC_MULTIPLIER = 2;
/* Fixed scoring table, same for every game: 1등 5점 · 2등 4점 · 3등 3점 ·
   4~6등 2점 · 7~9등 1점. RANK_POINTS[i] = points for exact finish rank i+1
   (used by the ranking-format day, which has a full 1~9 order). For a
   single-elimination bracket day there's no 3rd/4th playoff, so placement
   collapses to 5 tiers by how far a team got — champion, runner-up, lost
   semifinal, lost that round before it, lost the round before that — which
   line up exactly with RANK_POINTS' 5 distinct values. */
const RANK_POINTS = [5,4,3,2,2,2,1,1,1];
const TIER_POINTS = [5,4,3,2,1];

/* ============================== bracket utils ============================== */
/* A bracket is a binary tree, built by recursively splitting the seed list
   in half — not a padded power-of-2 grid. This is what makes the shape
   match a normal hand-drawn tournament chart: as many real, direct
   matchups as team count allows sit on the bottom row, and a bye is just
   an ordinary leaf that happens to be the lone member of its half, so its
   first game naturally lands one level up, at whatever height that is —
   no separate "auto-decided bye box" concept needed anywhere.
   bracket.nodes[id] = { id, a, b, winnerId, meta, parentId } where a/b are
   each either { teamId } (a seed) or { matchId } (another node's eventual
   winner). bracket.rootId is the final. */
function buildBracket(teamIds){
  let counter = 0;
  const nodes = {};
  const build = (ids, parentId)=>{
    if(ids.length===1) return { teamId: ids[0] };
    const mid = Math.floor(ids.length/2);
    const id = `m${counter++}`;
    const a = build(ids.slice(0,mid), id);
    const b = build(ids.slice(mid), id);
    nodes[id] = { id, a, b, winnerId:null, meta:{}, parentId: parentId||null };
    return { matchId: id };
  };
  const root = build(teamIds, null);
  return { nodes, rootId: root.matchId };
}
function getWinnerOf(bracket, matchId){
  const m = matchId && bracket.nodes[matchId];
  return m ? (m.winnerId || null) : null;
}
function getSideTeam(bracket, matchId, side){
  const node = bracket.nodes[matchId];
  const ref = side==="A" ? node.a : node.b;
  if(ref.teamId) return ref.teamId;
  return getWinnerOf(bracket, ref.matchId);
}
function clearDownstream(bracket, matchId){
  const node = bracket.nodes[matchId];
  const parentId = node.parentId;
  if(!parentId) return;
  const parent = bracket.nodes[parentId];
  if(parent.winnerId){
    parent.winnerId = null; parent.meta = {};
    clearDownstream(bracket, parentId);
  }
}
/* The seed order buildBracket() consumed (an in-order walk of the tree's
   leaves reproduces it exactly) — reconstructing it lets the seeding
   editor show/re-permute "who sits where" per day. */
function getRound1TeamOrder(bracket){
  const order = [];
  const walk = (ref)=>{
    if(ref.teamId){ order.push(ref.teamId); return; }
    const node = bracket.nodes[ref.matchId];
    walk(node.a); walk(node.b);
  };
  walk({ matchId: bracket.rootId });
  return order;
}
function bracketHasResults(bracket){
  return Object.values(bracket.nodes).some(m=>{
    const hasMeta = m.meta && (m.meta.firstMoverId || m.meta.setScore);
    return m.winnerId || hasMeta;
  });
}
/* How many real decisions deep a match sits, counted from the leaves —
   used to pick which visual row/label ("1라운드", "결승", …) it belongs
   to. A match pairing two seeds directly is depth 1 regardless of where
   in the tree it sits, so every "true first game" lines up together. */
function matchDepth(bracket, matchId){
  const node = bracket.nodes[matchId];
  const childDepth = (ref)=> ref.teamId ? 0 : matchDepth(bracket, ref.matchId);
  return 1 + Math.max(childDepth(node.a), childDepth(node.b));
}
/* Distance from this match up to the final, walking parent pointers —
   used for scoring tiers, since it fairly reflects "how many more wins
   would have reached the final" regardless of which branch a team is on. */
function hopsToFinal(bracket, matchId){
  let hops = 0, cur = bracket.nodes[matchId];
  while(cur.parentId){ hops++; cur = bracket.nodes[cur.parentId]; }
  return hops;
}
/* Inclusive [min,max] range of leaf seed-slots under this match, used to
   size/position its box against the other matches at its depth. */
function leafSpan(bracket, matchId, leafIndexOf){
  const node = bracket.nodes[matchId];
  const range = (ref)=> ref.teamId
    ? [leafIndexOf[ref.teamId], leafIndexOf[ref.teamId]]
    : leafSpan(bracket, ref.matchId, leafIndexOf);
  const [aMin,aMax] = range(node.a);
  const [bMin,bMax] = range(node.b);
  return [Math.min(aMin,bMin), Math.max(aMax,bMax)];
}

/* ============================== default state ============================== */
function defaultTeams(){
  return Array.from({length:9},(_,i)=>({ id:`t${i+1}`, name:`${i+1}팀`, color:PALETTE[i], image:null }));
}
function createDefaultState(){
  const teamIds = defaultTeams().map(t=>t.id);
  return {
    version:1,
    teams: defaultTeams(),
    days:{
      day1:{ bracket: buildBracket(teamIds) },
      day2:{ bracket: buildBracket(teamIds) },
      // entries' array order is the team-building course's turn order
      // (index+1 = "몇 번째로 진행하는지") — independent of ranking, which
      // is always computed from timeSec.
      day3:{ entries: teamIds.map(id=>({teamId:id, timeSec:null})) },
      day4:{ bracket: buildBracket(teamIds) },
    },
    scoring:{
      day1:{ execTeams:[] },
      day2:{ execTeams:[] },
      day3:{ execTeams:[] },
      day4:{ execTeams:[] },
    },
    display:{ activeDayKey:"day1" },
  };
}
function deepClone(o){ return JSON.parse(JSON.stringify(o)); }

/* ============================== scoring ============================== */
function getRankedEntries(day3){
  const entries = day3.entries;
  const withTime = entries.filter(e=>e.timeSec!=null).sort((a,b)=>a.timeSec-b.timeSec);
  const withoutTime = entries.filter(e=>e.timeSec==null);
  const ranked = withTime.map((e,i)=>({teamId:e.teamId, rank:i+1, timeSec:e.timeSec}));
  withoutTime.forEach(e=>ranked.push({teamId:e.teamId, rank:null, timeSec:null}));
  return ranked;
}
/* day3.entries' array order = turn order (1부터) — independent of the
   time-based ranking above; this is purely "who goes through the
   team-building course when", not a scoring concept. */
function turnOrderOf(day3){
  return Object.fromEntries(day3.entries.map((e,i)=>[e.teamId, i+1]));
}
/* Bracket placement has no 3rd-place playoff, so it collapses to 5 tiers
   by how close a team got to the final (champion / runner-up / lost a
   match feeding the final / lost one match earlier than that / lost
   earlier still) — TIER_POINTS' 5 values in order, using hopsToFinal so
   the tiering is fair across branches of uneven depth (a team's very
   first loss always lands in the same tier as anyone else's first loss,
   regardless of how many rounds their particular bracket half needed).
   Only decided matches award points, so in-progress days show partial
   scores. */
function computeBracketScores(dayState){
  const scores = {};
  const bracket = dayState.bracket;
  Object.keys(bracket.nodes).forEach(matchId=>{
    const m = bracket.nodes[matchId];
    if(!m.winnerId) return;
    const aId = getSideTeam(bracket, matchId, "A");
    const bId = getSideTeam(bracket, matchId, "B");
    const loserId = m.winnerId===aId ? bId : aId;
    if(matchId === bracket.rootId){
      scores[m.winnerId] = TIER_POINTS[0];
      if(loserId) scores[loserId] = TIER_POINTS[1];
    } else if(loserId){
      const tier = Math.min(hopsToFinal(bracket, matchId)+1, TIER_POINTS.length-1);
      scores[loserId] = TIER_POINTS[tier];
    }
  });
  return scores;
}
function computeRankingScores(dayState){
  const scores = {};
  getRankedEntries(dayState).forEach(e=>{
    if(e.rank) scores[e.teamId] = RANK_POINTS[e.rank-1]||0;
  });
  return scores;
}
/* The team(s) awarded 1등 (5점) for a day, before any exec multiplier —
   used only for the overall tie-break, never displayed as a score. */
function firstPlaceTeamsOf(dayState, format){
  if(format==="ranking"){
    return getRankedEntries(dayState).filter(e=>e.rank===1).map(e=>e.teamId);
  }
  const bracket = dayState.bracket;
  const final = bracket.nodes[bracket.rootId];
  return final.winnerId ? [final.winnerId] : [];
}
function computeOverall(state){
  const perDay = {};
  const totals = {}; const firstPlaceCount = {};
  state.teams.forEach(t=>{ totals[t.id]=0; firstPlaceCount[t.id]=0; });
  DAYS.forEach(d=>{
    const ds = state.days[d.key], cfg = state.scoring[d.key];
    let s = d.format==="ranking" ? computeRankingScores(ds) : computeBracketScores(ds);
    firstPlaceTeamsOf(ds, d.format).forEach(id=>{ firstPlaceCount[id] = (firstPlaceCount[id]||0)+1; });
    const applied = {};
    state.teams.forEach(t=>{
      let v = s[t.id]||0;
      if(cfg.execTeams && cfg.execTeams.includes(t.id)) v = v * EXEC_MULTIPLIER;
      applied[t.id] = v;
      totals[t.id] += v;
    });
    perDay[d.key] = applied;
  });
  return state.teams
    .map(t=>({
      ...t,
      total: Math.round(totals[t.id]*10)/10,
      firstPlaceCount: firstPlaceCount[t.id],
      perDay: DAYS.map(d=>Math.round((perDay[d.key][t.id]||0)*10)/10),
    }))
    .sort((a,b)=> b.total-a.total || b.firstPlaceCount-a.firstPlaceCount);
}
/* Each team's rank for TODAY's specific game only, not the running
   total — same scoring/exec-multiplier rule computeOverall uses for
   this one day, so the number shown here is exactly what today added
   to the cumulative score. Ties share a rank (1,2,2,4,…), the standard
   "competition ranking" convention. */
function computeTodayRanking(state, dayKey){
  const d = dayInfo(dayKey);
  const ds = state.days[dayKey], cfg = state.scoring[dayKey];
  const raw = d.format==="ranking" ? computeRankingScores(ds) : computeBracketScores(ds);
  const scored = state.teams
    .map(t=>{
      let v = raw[t.id]||0;
      if(cfg.execTeams && cfg.execTeams.includes(t.id)) v = v*EXEC_MULTIPLIER;
      return { ...t, score: Math.round(v*10)/10 };
    })
    .sort((a,b)=> b.score-a.score);
  let rank = 0, prevScore = null;
  return scored.map((t,i)=>{
    if(t.score!==prevScore){ rank = i+1; prevScore = t.score; }
    return { ...t, rank };
  });
}

/* ============================== excel export ============================== */
function teamName(state, id){
  const t = state.teams.find(t=>t.id===id);
  return t ? t.name : "-";
}
function overallSheetRows(state){
  const overall = computeOverall(state);
  const rows = [["순위","팀명", ...DAYS.map(d=>`${d.label} ${d.game}`), "총점", "1등 횟수"]];
  overall.forEach((t,i)=>{ rows.push([ i+1, t.name, ...t.perDay, t.total, t.firstPlaceCount ]); });
  return rows;
}
function bracketSheetRows(state, dayKey){
  const d = dayInfo(dayKey);
  const bracket = state.days[dayKey].bracket;
  const header = ["라운드","경기","팀A","팀B","승리팀"];
  if(d.hasFirstMover) header.push("선공");
  if(d.hasSetScore) header.push("세트스코어");
  const rows = [header];
  const matchIds = Object.keys(bracket.nodes);
  const depthOf = {}; matchIds.forEach(id=>{ depthOf[id] = matchDepth(bracket,id); });
  const maxDepth = Math.max(...matchIds.map(id=>depthOf[id]));
  const leafOrder = getRound1TeamOrder(bracket);
  const leafIndexOf = {}; leafOrder.forEach((tid,idx)=>{ leafIndexOf[tid]=idx; });
  const spanOf = {}; matchIds.forEach(id=>{ spanOf[id] = leafSpan(bracket,id,leafIndexOf); });
  // earliest-playable matches first, left-to-right within a depth
  matchIds.sort((x,y)=> depthOf[x]-depthOf[y] || spanOf[x][0]-spanOf[y][0]);
  const matchNumInDepth = {};
  matchIds.forEach(id=>{
      const m = bracket.nodes[id];
      const depth = depthOf[id];
      const roundLabel = depth===maxDepth ? "결승" : `${depth}라운드`;
      matchNumInDepth[depth] = (matchNumInDepth[depth]||0) + 1;
      const aId = getSideTeam(bracket, id, "A");
      const bId = getSideTeam(bracket, id, "B");
      const row = [
        roundLabel, `${matchNumInDepth[depth]}경기`,
        aId ? teamName(state,aId) : "-",
        bId ? teamName(state,bId) : "-",
        m.winnerId ? teamName(state,m.winnerId) : "-",
      ];
      if(d.hasFirstMover) row.push(m.meta && m.meta.firstMoverId ? teamName(state,m.meta.firstMoverId) : "-");
      if(d.hasSetScore) row.push(m.meta && m.meta.setScore ? m.meta.setScore : "-");
      rows.push(row);
  });
  return rows;
}
function rankingSheetRows(state, dayKey){
  const rows = [["순위","팀명","기록"]];
  getRankedEntries(state.days[dayKey]).forEach(e=>{
    rows.push([ e.rank ? `${e.rank}위` : "-", teamName(state,e.teamId), e.timeSec!=null ? fmtTime(e.timeSec) : "-" ]);
  });
  return rows;
}
function safeSheetName(name){
  return name.replace(/[:\\/?*\[\]]/g," ").slice(0,31);
}
function buildResultWorkbook(state){
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(overallSheetRows(state)), safeSheetName("종합순위"));
  DAYS.forEach(d=>{
    const rows = d.format==="ranking" ? rankingSheetRows(state, d.key) : bracketSheetRows(state, d.key);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), safeSheetName(`${d.label} ${d.game}`));
  });
  return wb;
}
/* A single day's own results — a smaller, focused file meant to be
   attached alongside the poster prompt below, rather than the full
   4-day export. */
function buildDayResultWorkbook(state, dayKey){
  const d = dayInfo(dayKey);
  const wb = XLSX.utils.book_new();
  const ranking = computeTodayRanking(state, dayKey);
  const rankRows = [["순위","팀명","오늘 점수"], ...ranking.map(t=>[t.rank, t.name, t.score])];
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rankRows), safeSheetName("오늘 순위"));
  const detailRows = d.format==="ranking" ? rankingSheetRows(state, dayKey) : bracketSheetRows(state, dayKey);
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(detailRows), safeSheetName(`${d.label} 상세`));
  return wb;
}
/* Plain-text prompt (for pasting into an image-generating AI later,
   outside this offline app) summarizing the day's winner and full
   ranking, meant to be downloaded alongside buildDayResultWorkbook(). */
function buildPosterPrompt(state, dayKey){
  const d = dayInfo(dayKey);
  const dayState = state.days[dayKey];
  const ranking = computeTodayRanking(state, dayKey);
  // The point-tier ranking above is score-based, so mid-tournament a team
  // that already lost its first match can outrank teams still alive but
  // not yet eliminated (they haven't scored yet). That's correct for a
  // live leaderboard but wrong for "who won" — use the bracket's actual
  // decided final (or, for the ranking day, whoever holds rank 1) instead,
  // and say so plainly if the day isn't finished yet.
  const championIds = firstPlaceTeamsOf(dayState, d.format);
  const championName = championIds.length ? teamName(state, championIds[0]) : "(아직 결정되지 않음 — 결승/최종 순위 확정 후 다시 받아주세요)";
  const rankingLines = ranking.map(t=>`${t.rank}위 — ${t.name} (${t.score}점)`).join("\n");
  return [
    `[썸머탈출 페스티벌 ${d.label} 축하 포스터 제작 요청]`,
    ``,
    `아래 정보를 반영해서, 여름 휴양지/페스티벌 분위기의 밝고 역동적인 사내 행사 축하 포스터를 만들어주세요.`,
    ``,
    `- 행사: 썸머탈출 페스티벌 ${d.label} · ${d.game}`,
    `- 날짜/장소: ${d.date} · ${d.place}`,
    `- 오늘의 우승팀: ${championName} 🏆`,
    ``,
    `[오늘 전체 순위]`,
    rankingLines,
    ``,
    `우승팀 이름과 트로피/왕관 이미지를 가장 크고 눈에 띄게 배치하고, 나머지 순위도 보기 좋게 함께 넣어주세요.`,
    `참고: 함께 첨부한 엑셀 파일에 이 Day의 상세 경기 결과(대진표/세트 스코어 등)가 들어있으니 필요하면 참고하세요.`,
  ].join("\n");
}

/* ============================== misc utils ============================== */
function fmtTime(sec){
  if(sec==null || isNaN(sec)) return "-";
  const m = Math.floor(sec/60), s = (sec%60);
  return `${String(m).padStart(2,"0")}:${s.toFixed(2).padStart(5,"0")}`;
}
function parseTimeInput(str){
  if(str==null || str==="") return null;
  const s = String(str).trim();
  if(s.includes(":")){
    const [m,ss] = s.split(":");
    const v = (parseFloat(m)||0)*60 + (parseFloat(ss)||0);
    return isNaN(v) ? null : v;
  }
  const v = parseFloat(s);
  return isNaN(v) ? null : v;
}
function compressImage(file, maxDim, quality){
  return new Promise((resolve, reject)=>{
    const reader = new FileReader();
    reader.onerror = ()=>reject(new Error("read_failed"));
    reader.onload = ()=>{
      const img = new Image();
      img.onerror = ()=>reject(new Error("decode_failed"));
      img.onload = ()=>{
        let { width, height } = img;
        if(width>height){ if(width>maxDim){ height=Math.round(height*maxDim/width); width=maxDim; } }
        else { if(height>maxDim){ width=Math.round(width*maxDim/height); height=maxDim; } }
        const canvas = document.createElement("canvas");
        canvas.width=width; canvas.height=height;
        canvas.getContext("2d").drawImage(img,0,0,width,height);
        resolve(canvas.toDataURL("image/jpeg", quality));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

/* ============================== local persistence ============================== */
/* A saved bracket from an older build of this app can use a shape this
   code no longer understands (this app has changed how it represents a
   bracket more than once) — loading it as-is would crash before anything
   ever renders, leaving a blank page with no way to reach the reset
   button. So validate the shape first and silently fall back to a fresh
   default state instead of trusting whatever's in localStorage. */
function isValidBracket(bracket){
  return !!(bracket && bracket.nodes && bracket.rootId && bracket.nodes[bracket.rootId]);
}
function loadInitialState(){
  try{
    const raw = localStorage.getItem(STORAGE_KEY);
    if(raw){
      const parsed = JSON.parse(raw);
      const days = parsed && parsed.days;
      const bracketDaysOk = days && DAYS.every(d=>
        d.format!=="bracket" || isValidBracket(days[d.key] && days[d.key].bracket));
      if(bracketDaysOk) return parsed;
    }
  }catch(e){ /* corrupt or unavailable — fall back to defaults */ }
  return createDefaultState();
}
function useLocalState(){
  const [state, setState] = useState(loadInitialState);
  const [saveError, setSaveError] = useState(null);
  const update = useCallback((fn)=>{
    setState(prev=>{
      const next = fn(deepClone(prev));
      if(!next) return prev;
      try{
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
        if(saveError) setSaveError(null);
      }catch(e){
        setSaveError("이 브라우저에 저장하지 못했습니다(저장 공간 부족일 수 있어요). 입력은 화면에 유지되지만 새로고침하면 사라질 수 있습니다.");
      }
      return next;
    });
  },[saveError]);
  return { state, update, saveError };
}

/* ============================== small components ============================== */
function TeamChip({team, size, exec, champion}){
  if(!team) return <span className="slot tbd">TBD</span>;
  return (
    <span className="team-chip">
      {champion && <span className="crown" title="우승">👑</span>}
      {team.image
        ? <img className={"team-avatar" + (size==="big"?" big":"") + (champion?" champion":"")} src={team.image} />
        : <span className={"dot" + (champion?" champion":"")} style={{width:size==="big"?52:22,height:size==="big"?52:22,borderRadius:"50%",background:team.color,flex:"none",border:"2px solid rgba(255,255,255,.25)"}}></span>}
      <span className="team-name">{team.name}</span>
      {exec && <span className="exec-badge" title={`임원 참여 ×${EXEC_MULTIPLIER}`}>⚡×{EXEC_MULTIPLIER}</span>}
    </span>
  );
}

/* ============================== game illustrations (flat, transparent) ============================== */
function JengaIllustration({size}){
  return <img src={GAME_IMAGES.jenga} alt="" style={{width:size, height:"auto"}} />;
}
function KartIllustration({size}){
  return <img src={GAME_IMAGES.kart} alt="" style={{width:size, height:"auto"}} />;
}
function JumpRopeIllustration({size}){
  return <img src={GAME_IMAGES.jumprope} alt="" style={{width:size, height:"auto"}} />;
}
function PingPongIllustration({size}){
  return <img src={GAME_IMAGES.pingpong} alt="" style={{width:size, height:"auto"}} />;
}
function CurlingIllustration({size}){
  return <img src={GAME_IMAGES.curling} alt="" style={{width:size, height:"auto"}} />;
}
const ILLUSTRATIONS = {
  jenga: JengaIllustration, kart: KartIllustration, pingpong: PingPongIllustration,
  jumprope: JumpRopeIllustration, curling: CurlingIllustration,
};
/* which illustration(s) represent each day's game on the display screen */
const DAY_ILLUSTRATIONS = {
  day1: [{ id:"jenga", label:"자이언트 젠가" }],
  day2: [{ id:"kart", label:"카트라이더" }],
  day3: [{ id:"jumprope", label:"단체줄넘기" }, { id:"pingpong", label:"탁구공넣기" }],
  day4: [{ id:"curling", label:"컬링" }],
};

/* ============================== bracket board ============================== */
/* Every match is positioned individually — grid-row by its depth (1라운드
   at the bottom, 결승 at the top) and grid-column by the span of leaf
   seed-slots under it — rather than forced into uniform full-width rows.
   That's what keeps every bend point honest: a box's column exactly
   matches its own branch, so even a connector that spans more than one
   row (a shorter branch reaching the final faster than a longer one)
   never runs through a box it has nothing to do with. */
function BracketBoard({ bracket, teamsById, editable, onSetWinner, onResetWinner, renderExtra, execTeamIds }){
  const containerRef = useRef(null);
  const matchRefs = useRef({});
  const [lines, setLines] = useState([]);
  const setRef = (id) => (el)=>{ matchRefs.current[id] = el; };
  const isExec = (id)=> !!(id && execTeamIds && execTeamIds.includes(id));

  const matchIds = Object.keys(bracket.nodes);
  const leafOrder = getRound1TeamOrder(bracket);
  const leafIndexOf = {};
  leafOrder.forEach((tid,idx)=>{ leafIndexOf[tid] = idx; });
  const leafCount = leafOrder.length;

  const depthOf = {};
  matchIds.forEach(id=>{ depthOf[id] = matchDepth(bracket, id); });
  const maxDepth = Math.max(...matchIds.map(id=>depthOf[id]));

  const spanOf = {};
  matchIds.forEach(id=>{ spanOf[id] = leafSpan(bracket, id, leafIndexOf); });

  // the matches currently playable: the shallowest depth with an
  // undecided match whose both sides are already known.
  let currentIds = [];
  let currentDepth = Infinity;
  matchIds.forEach(id=>{
    const m = bracket.nodes[id];
    if(m.winnerId) return;
    const aId = getSideTeam(bracket, id, "A");
    const bId = getSideTeam(bracket, id, "B");
    if(!aId || !bId) return;
    if(depthOf[id] < currentDepth){ currentDepth = depthOf[id]; currentIds = [id]; }
    else if(depthOf[id] === currentDepth){ currentIds.push(id); }
  });

  const recompute = useCallback(()=>{
    const cont = containerRef.current;
    if(!cont) return;
    const cRect = cont.getBoundingClientRect();
    const newLines = [];
    matchIds.forEach(id=>{
      const node = bracket.nodes[id];
      if(!node.parentId) return; // the final has nothing above it
      const srcEl = matchRefs.current[id];
      const tgtEl = matchRefs.current[node.parentId];
      if(!srcEl || !tgtEl) return;
      const sr = srcEl.getBoundingClientRect(), tr = tgtEl.getBoundingClientRect();
      // source sits below its parent (possibly several rows below, on a
      // branch shorter than its sibling): line exits the top of the
      // source box and enters the bottom of the parent's box.
      newLines.push({
        x1: sr.left + sr.width/2 - cRect.left, y1: sr.top - cRect.top,
        x2: tr.left + tr.width/2 - cRect.left, y2: tr.bottom - cRect.top,
        decided: !!node.winnerId,
      });
    });
    setLines(newLines);
  // eslint-disable-next-line
  },[bracket]);

  useLayoutEffect(()=>{
    recompute();
    // the embedded webfont can swap in after this first measurement (font-display:swap),
    // reflowing match-card text and shifting box positions — recompute again once it's ready.
    if(document.fonts && document.fonts.ready){
      document.fonts.ready.then(()=>recompute());
    }
    const safetyTimer = setTimeout(recompute, 400);
    const onResize = ()=>recompute();
    window.addEventListener("resize", onResize);
    // watch every match box individually, not just the container — a box's
    // own size can change (font metrics settling, text re-wrapping) without
    // the container's overall bounding box changing, which the container
    // observer alone wouldn't catch.
    const ro = new ResizeObserver(()=>recompute());
    if(containerRef.current) ro.observe(containerRef.current);
    Object.values(matchRefs.current).forEach(el=>{ if(el) ro.observe(el); });
    return ()=>{ window.removeEventListener("resize", onResize); ro.disconnect(); clearTimeout(safetyTimer); };
  },[recompute]);

  return (
    <div className="bracket-scroll">
      <div className="bracket-grid" ref={containerRef}
        style={{ gridTemplateColumns:`repeat(${leafCount}, var(--bracket-col))`, gridTemplateRows:`repeat(${maxDepth*2}, auto)` }}>
        <svg className="connectors">
          {lines.map((l,idx)=>{
            const midY = (l.y1+l.y2)/2;
            const d = `M ${l.x1} ${l.y1} V ${midY} H ${l.x2} V ${l.y2}`;
            return <path key={idx} d={d} className={l.decided?"decided":""} />;
          })}
        </svg>
        {Array.from({length:maxDepth},(_,k)=>k+1).map(depth=>{
          const labelRow = (maxDepth-depth)*2 + 1;
          return (
            <div className={"round-label" + (depth===currentDepth?" round-current":"")} key={"label"+depth}
              style={{ gridColumn:"1 / -1", gridRow:labelRow }}>
              {depth===currentDepth ? "▶ " : ""}{depth===maxDepth ? "결승" : `${depth}라운드`}
            </div>
          );
        })}
        {matchIds.map(id=>{
          const m = bracket.nodes[id];
          const [minL, maxL] = spanOf[id];
          const depth = depthOf[id];
          const gridRow = (maxDepth-depth)*2 + 2;
          const aId = getSideTeam(bracket, id, "A");
          const bId = getSideTeam(bracket, id, "B");
          const aTeam = aId ? teamsById[aId] : null;
          const bTeam = bId ? teamsById[bId] : null;
          const canPick = editable && aTeam && bTeam;
          const rowClass = (tid)=> tid && m.winnerId ? (tid===m.winnerId?"slot winner":"slot loser") : "slot";
          const isChampion = (tid)=> id===bracket.rootId && !!tid && tid===m.winnerId;
          const pick = (teamId)=>{
            if(!canPick) return;
            const changing = !!m.winnerId && m.winnerId!==teamId;
            if(changing && !window.confirm("정말 변경하시겠습니까? 다음 라운드에 반영된 결과도 함께 초기화됩니다.")) return;
            onSetWinner(id, teamId);
          };
          const canReset = editable && m.winnerId;
          const resetWinner = (e)=>{
            e.stopPropagation();
            if(!window.confirm("승자를 초기화할까요? 다음 라운드에 반영된 결과도 함께 초기화됩니다.")) return;
            onResetWinner(id);
          };
          return (
            <div className={"match" + (m.winnerId?" decided":"") + (currentIds.includes(id)?" match-current":"")}
              key={id} ref={setRef(id)} style={{ gridColumn:`${minL+1} / ${maxL+2}`, gridRow }}>
              {canReset &&
                <button className="match-reset-btn" onClick={resetWinner} title="승자 초기화">↺</button>}
              {canPick ? (
                <button className="slot-btn" onClick={()=>pick(aId)}>
                  <span className={rowClass(aId)} style={{width:"100%"}}><TeamChip team={aTeam} exec={isExec(aId)} champion={isChampion(aId)} /></span>
                </button>
              ) : (
                <div className={rowClass(aId) + (!aTeam?" tbd":"")}><TeamChip team={aTeam} exec={isExec(aId)} champion={isChampion(aId)} /></div>
              )}
              {canPick ? (
                <button className="slot-btn" onClick={()=>pick(bId)}>
                  <span className={rowClass(bId)} style={{width:"100%"}}><TeamChip team={bTeam} exec={isExec(bId)} champion={isChampion(bId)} /></span>
                </button>
              ) : (
                <div className={rowClass(bId) + (!bTeam?" tbd":"")}><TeamChip team={bTeam} exec={isExec(bId)} champion={isChampion(bId)} /></div>
              )}
              {renderExtra && aTeam && bTeam && renderExtra(m, id)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ============================== ranking board ============================== */
function RankingBoard({ day3, teamsById, execTeamIds, editable, onSetTime }){
  const ranked = getRankedEntries(day3).slice().sort((a,b)=>{
    if(a.rank==null) return 1; if(b.rank==null) return -1; return a.rank-b.rank;
  });
  const turnOrder = turnOrderOf(day3);
  const showTimeInput = editable;
  return (
    <div className="ranking-list">
      {ranked.map((e,idx)=>{
        const t = teamsById[e.teamId];
        const rc = e.rank===1?"r1":e.rank===2?"r2":e.rank===3?"r3":"";
        return (
          <div className={"rank-row " + rc} key={e.teamId}>
            <div className="rank-num">{e.rank || "-"}</div>
            <span className="turn-badge" title="진행순서">{turnOrder[e.teamId]}번째 진행</span>
            <TeamChip team={t} exec={execTeamIds && execTeamIds.includes(e.teamId)} champion={e.rank===1} />
            {showTimeInput ? (
              <input type="text" className="rank-time-input" placeholder="mm:ss"
                defaultValue={e.timeSec!=null?fmtTime(e.timeSec):""}
                onClick={(ev)=>ev.stopPropagation()}
                onBlur={(ev)=>onSetTime(e.teamId, ev.target.value)} />
            ) : (
              <div className="rank-time">{e.timeSec!=null ? fmtTime(e.timeSec) : "미기록"}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ============================== display view ============================== */
function DisplayView({ state, update }){
  const teamsById = useMemo(()=>Object.fromEntries(state.teams.map(t=>[t.id,t])),[state.teams]);
  const activeKey = state.display.activeDayKey;
  const info = dayInfo(activeKey);
  const execTeamIds = state.scoring[activeKey].execTeams;
  const overall = useMemo(()=>computeOverall(state),[state]);
  const todayRanking = useMemo(()=>computeTodayRanking(state, activeKey),[state, activeKey]);
  const setDay3Time = (teamId, val)=> update(s=>{
    const e = s.days.day3.entries.find(x=>x.teamId===teamId);
    e.timeSec = parseTimeInput(val);
    return s;
  });

  return (
    <div>
      <div className="topbar">
        <div className="brand"><span className="emoji">🏝️</span><h1>썸머탈출 페스티벌</h1></div>
        <div className="daybadge">
          <span className="tag">{info.label}</span>
          <span className="game">{info.game}</span>
          <span className="meta">{info.date} · {info.place} · {info.unit}</span>
        </div>
      </div>
      <div className="display-wrap">
        <div className="today-header-row">
          <div>
            <div className="panel-title"><span className="bar"></span>오늘의 경기 현황 · {info.game}</div>
            <div className="exec-legend">⚡×{EXEC_MULTIPLIER} <b>임원 참여 2배룰 적용</b></div>
          </div>
          <div className="top9-stack">
            <div className="top9-compact">
              <div className="top9-compact-title">종합 순위 TOP 9 (누적)</div>
              <div className="top9-compact-grid">
                {overall.map((t,idx)=>(
                  <div className={"top9c-chip" + (idx<3?" top3":"")} key={t.id} title={t.firstPlaceCount>0?`🥇×${t.firstPlaceCount}`:undefined}>
                    <span className="top9c-rank">{idx+1}</span>
                    {t.image
                      ? <img className="top9c-dot" src={t.image} style={{objectFit:"cover"}} />
                      : <span className="top9c-dot" style={{background:t.color}}></span>}
                    <span className="top9c-name">{t.name}</span>
                    <span className="top9c-score">{t.total}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="top9-compact">
              <div className="top9-compact-title">오늘 · {info.game} 순위</div>
              <div className="top9-compact-grid">
                {todayRanking.map(t=>(
                  <div className={"top9c-chip" + (t.rank<=3?" top3":"")} key={t.id}>
                    <span className="top9c-rank">{t.rank}</span>
                    {t.image
                      ? <img className="top9c-dot" src={t.image} style={{objectFit:"cover"}} />
                      : <span className="top9c-dot" style={{background:t.color}}></span>}
                    <span className="top9c-name">{t.name}</span>
                    <span className="top9c-score">{t.score}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
        <div className="today-panel-full">
          {info.format==="bracket"
            ? <InteractiveBracketSection dayKey={activeKey} state={state} update={update} teamsById={teamsById} />
            : <RankingBoard day3={state.days.day3} teamsById={teamsById} execTeamIds={execTeamIds} editable={true} onSetTime={setDay3Time} />}
        </div>
      </div>
    </div>
  );
}

/* ============================== admin: day1 first-mover picker ============================== */
/* Admin just picks who goes first directly — the timer itself is run outside the app. */
function FirstMoverPicker({ match, onUpdate, onReset, teamsById, aId, bId }){
  const current = match.meta && match.meta.firstMoverId;
  const pick = (teamId)=> onUpdate({ firstMoverId: teamId, firstMoverLabel: "선공" });
  return (
    <div className="mini-widget" onClick={e=>e.stopPropagation()}>
      <div className="mini-widget-row">
        <span className="mini-label">선공</span>
        <button className={"mini-pick" + (current===aId?" active":"")} onClick={()=>pick(aId)}>
          {teamsById[aId] && teamsById[aId].name}
        </button>
        <button className={"mini-pick" + (current===bId?" active":"")} onClick={()=>pick(bId)}>
          {teamsById[bId] && teamsById[bId].name}
        </button>
        {current &&
          <button className="mini-reset" title="선공 초기화" onClick={()=>{ if(window.confirm("선공 기록을 초기화할까요?")) onReset(); }}>↺</button>}
      </div>
    </div>
  );
}

/* ============================== day2: set-score picker (3판 2선승) ============================== */
/* Entering the set score decides the match automatically — first to 2 wins. */
function SetScorePicker({ match, onUpdate, onWinner, onReset, aId, bId }){
  const setA = (match.meta && match.meta.setA) || 0;
  const setB = (match.meta && match.meta.setB) || 0;
  const bump = (side, delta)=>{
    let a = setA, b = setB;
    if(side==="A") a = Math.max(0, Math.min(2, a+delta));
    else b = Math.max(0, Math.min(2, b+delta));
    onUpdate({ setA:a, setB:b, setScore:`${a}:${b}` });
    if(a===2 && b<2) onWinner(aId);
    else if(b===2 && a<2) onWinner(bId);
  };
  return (
    <div className="mini-widget" onClick={e=>e.stopPropagation()}>
      <div className="mini-widget-row">
        <span className="mini-label">세트</span>
        <button className="mini-stepper-btn" onClick={()=>bump("A",-1)}>－</button>
        <span className="mini-num">{setA}</span>
        <button className="mini-stepper-btn" onClick={()=>bump("A",1)}>＋</button>
        <span className="mini-colon">:</span>
        <button className="mini-stepper-btn" onClick={()=>bump("B",-1)}>－</button>
        <span className="mini-num">{setB}</span>
        <button className="mini-stepper-btn" onClick={()=>bump("B",1)}>＋</button>
        {(setA>0 || setB>0) &&
          <button className="mini-reset" title="점수 초기화" onClick={()=>{ if(window.confirm("세트 스코어를 초기화할까요? (승자는 별도로 초기화해야 합니다)")) onReset(); }}>↺</button>}
      </div>
    </div>
  );
}

/* ============================== interactive bracket (shared: admin + display) ============================== */
/* Winner picking, first-mover, set-score and their resets — used both in the
   admin tab and directly on the display screen, since flipping into admin
   mode for every single match update isn't practical during a live event. */
function InteractiveBracketSection({ dayKey, state, update, teamsById }){
  const info = dayInfo(dayKey);
  const dayState = state.days[dayKey];
  const execTeamIds = state.scoring[dayKey].execTeams;

  const setWinner = (matchId,teamId)=> update(s=>{
    const b = s.days[dayKey].bracket;
    const m = b.nodes[matchId];
    if(m.winnerId===teamId) return null;
    m.winnerId = teamId;
    clearDownstream(b, matchId);
    return s;
  });
  const resetWinner = (matchId)=> update(s=>{
    const b = s.days[dayKey].bracket;
    const m = b.nodes[matchId];
    if(!m.winnerId) return null;
    m.winnerId = null;
    clearDownstream(b, matchId);
    return s;
  });
  const setMeta = (matchId,patch)=> update(s=>{
    const m = s.days[dayKey].bracket.nodes[matchId];
    m.meta = { ...m.meta, ...patch };
    return s;
  });
  const resetMeta = (matchId,keys)=> update(s=>{
    const m = s.days[dayKey].bracket.nodes[matchId];
    const meta = { ...m.meta };
    keys.forEach(k=>delete meta[k]);
    m.meta = meta;
    return s;
  });

  return (
    <BracketBoard
      bracket={dayState.bracket}
      teamsById={teamsById}
      editable={true}
      execTeamIds={execTeamIds}
      onSetWinner={setWinner}
      onResetWinner={resetWinner}
      renderExtra={(m,matchId)=>{
        const aId = getSideTeam(dayState.bracket, matchId, "A");
        const bId = getSideTeam(dayState.bracket, matchId, "B");
        return (
          <div>
            {info.hasFirstMover &&
              <FirstMoverPicker match={m} teamsById={teamsById} aId={aId} bId={bId}
                onUpdate={(patch)=>setMeta(matchId,patch)}
                onReset={()=>resetMeta(matchId,["firstMoverId","firstMoverLabel"])} />}
            {info.hasSetScore &&
              <SetScorePicker match={m} aId={aId} bId={bId}
                onUpdate={(patch)=>setMeta(matchId,patch)}
                onWinner={(teamId)=>setWinner(matchId,teamId)}
                onReset={()=>resetMeta(matchId,["setScore","setA","setB"])} />}
          </div>
        );
      }}
    />
  );
}

/* ============================== admin: bracket seeding editor ============================== */
/* Which of the 9 teams sits in which round-1 slot — configured per day,
   since the day-to-day matchups aren't the same 9 teams in the same spots. */
function SeedingEditor({ dayKey, bracket, teams, update }){
  const order = getRound1TeamOrder(bracket);
  const setPosition = (posIdx, teamId)=>{
    if(order[posIdx]===teamId) return;
    if(bracketHasResults(bracket) && !window.confirm("자리를 바꾸면 이 Day에 입력된 모든 경기 결과가 초기화됩니다. 계속하시겠습니까?")) return;
    update(s=>{
      const curOrder = getRound1TeamOrder(s.days[dayKey].bracket);
      const fromIdx = curOrder.indexOf(teamId);
      if(fromIdx===posIdx) return null;
      const tmp = curOrder[posIdx];
      curOrder[posIdx] = teamId;
      curOrder[fromIdx] = tmp;
      s.days[dayKey].bracket = buildBracket(curOrder);
      return s;
    });
  };
  return (
    <div className="card">
      <h3>대진 편성 (자리 배정)</h3>
      <p style={{color:"var(--sub)",fontSize:12,marginTop:-6,marginBottom:12}}>
        이 Day의 대진표에 어떤 팀을 어느 자리에 배치할지 정하세요. Day마다 매치업을 다르게 구성할 수 있습니다.
        같은 자리에 다른 팀을 고르면 원래 있던 팀과 자동으로 자리가 바뀝니다.
      </p>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(160px,1fr))",gap:10}}>
        {order.map((teamId,idx)=>(
          <div className="field" key={idx} style={{marginBottom:0}}>
            <label>{idx+1}번 자리</label>
            <select value={teamId} onChange={(e)=>setPosition(idx, e.target.value)}>
              {teams.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============================== admin: day tab ============================== */
function DayTab({ dayKey, state, update, teamsById }){
  const info = dayInfo(dayKey);
  const dayState = state.days[dayKey];

  if(info.format==="bracket"){
    return (
      <div>
        <SeedingEditor dayKey={dayKey} bracket={dayState.bracket} teams={state.teams} update={update} />
        <div className="card">
          <div className="day-header">
            <div className="title">{info.label} · {info.game}</div>
            <div className="sub">{info.date} · {info.place} · {info.unit}</div>
          </div>
          <InteractiveBracketSection dayKey={dayKey} state={state} update={update} teamsById={teamsById} />
        </div>
      </div>
    );
  }

  // day3 ranking editor — 진행순서(줄 세우는 순서)는 완전히 별개 개념이라
  // 시간과 무관하게 관리자가 직접 정하고, 순위는 항상 완료 시간으로만 계산된다.
  const execTeamIds = state.scoring[dayKey].execTeams;
  const entries = dayState.entries;
  const setTime = (teamId, val)=> update(s=>{
    const e = s.days.day3.entries.find(x=>x.teamId===teamId);
    e.timeSec = parseTimeInput(val);
    return s;
  });
  const setTurnOrder = (teamId, newOrder)=> update(s=>{
    const arr = s.days.day3.entries;
    const idx = arr.findIndex(x=>x.teamId===teamId);
    const newIdx = Math.max(0, Math.min(arr.length-1, newOrder-1));
    if(idx<0 || idx===newIdx) return null;
    const [item] = arr.splice(idx,1);
    arr.splice(newIdx,0,item);
    return s;
  });
  const ranked = getRankedEntries(dayState);
  const rankOf = Object.fromEntries(ranked.map(e=>[e.teamId,e.rank]));
  const turnOrder = turnOrderOf(dayState);

  return (
    <div className="card">
      <div className="day-header">
        <div className="title">{info.label} · {info.game}</div>
        <div className="sub">{info.date} · {info.place} · {info.unit}</div>
      </div>
      <p style={{color:"var(--sub)",fontSize:12,marginTop:-6,marginBottom:12}}>
        진행순서는 팀빌딩 코스를 도는 순서(현장 대기열)이고, 순위는 완료 시간으로 자동 계산됩니다 — 서로 영향을 주지 않습니다.
      </p>
      <div style={{display:"flex",flexDirection:"column",gap:8}}>
        {entries.map((e,idx)=>{
          const t = teamsById[e.teamId];
          return (
            <div className="rank-edit-row" key={e.teamId}>
              <select value={turnOrder[e.teamId]} style={{width:90}}
                onChange={(ev)=>setTurnOrder(e.teamId, parseInt(ev.target.value,10))}>
                {entries.map((_,i)=><option key={i} value={i+1}>{i+1}번째</option>)}
              </select>
              <div style={{width:30,textAlign:"center",fontWeight:900,color:"var(--gold)"}}>{rankOf[e.teamId]||"-"}</div>
              <TeamChip team={t} exec={execTeamIds.includes(e.teamId)} champion={rankOf[e.teamId]===1} />
              <input type="text" style={{marginLeft:"auto",width:100}} placeholder="mm:ss"
                defaultValue={e.timeSec!=null?fmtTime(e.timeSec):""}
                onBlur={(ev)=>setTime(e.teamId, ev.target.value)} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ============================== admin: teams tab ============================== */
function TeamsTab({ state, update }){
  const [busy, setBusy] = useState(null);
  const rename = (id, name)=> update(s=>{ s.teams.find(t=>t.id===id).name = name; return s; });
  const recolor = (id, color)=> update(s=>{ s.teams.find(t=>t.id===id).color = color; return s; });
  const upload = async (id, file)=>{
    if(!file) return;
    setBusy(id);
    try{
      const dataUrl = await compressImage(file, 240, 0.82);
      update(s=>{ s.teams.find(t=>t.id===id).image = dataUrl; return s; });
    }catch(e){ window.alert("이미지 처리에 실패했습니다."); }
    setBusy(null);
  };
  return (
    <div className="card">
      <h3>팀 정보 관리 (총 9팀)</h3>
      <div className="grid-teams">
        {state.teams.map(t=>(
          <div className="team-edit-card" key={t.id}>
            <div style={{position:"relative"}}>
              {t.image
                ? <img className="team-avatar big" src={t.image} />
                : <div className="team-avatar big" style={{background:t.color}}></div>}
            </div>
            <div style={{flex:1}}>
              <div className="field">
                <label>팀명</label>
                <input type="text" defaultValue={t.name} onBlur={(e)=>rename(t.id, e.target.value)} />
              </div>
              <div style={{display:"flex",gap:10,alignItems:"center"}}>
                <div className="field" style={{marginBottom:0}}>
                  <label>팀 컬러</label>
                  <input type="color" defaultValue={t.color} onChange={(e)=>recolor(t.id, e.target.value)} />
                </div>
                <div className="field" style={{marginBottom:0,flex:1}}>
                  <label>팀장 이미지</label>
                  <input type="file" accept="image/*" onChange={(e)=>upload(t.id, e.target.files[0])} disabled={busy===t.id} />
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============================== admin: scoring tab ============================== */
function ScoringTab({ state, update }){
  const toggleExec = (dayKey, teamId)=> update(s=>{
    const cfg = s.scoring[dayKey];
    const i = cfg.execTeams.indexOf(teamId);
    if(i>=0) cfg.execTeams.splice(i,1); else cfg.execTeams.push(teamId);
    return s;
  });

  return (
    <div>
      <div className="card">
        <h3>배점 방식 (모든 Day 공통, 고정)</h3>
        <table className="table">
          <thead><tr><th>1등</th><th>2등</th><th>3등</th><th>4~6등</th><th>7~9등</th></tr></thead>
          <tbody><tr><td>5점</td><td>4점</td><td>3점</td><td>2점</td><td>1점</td></tr></tbody>
        </table>
        <div style={{fontSize:12,color:"var(--sub)",marginTop:8}}>
          토너먼트(Day1·2·4)는 3·4위 결정전이 없어 준결승 탈락 두 팀이 공동 3등으로 계산됩니다.
          종합 점수가 동일하면 <b style={{color:"var(--gold)"}}>게임별 1등 횟수가 많은 팀</b>이 앞섭니다.
        </div>
      </div>
      {DAYS.map(d=>{
        const cfg = state.scoring[d.key];
        return (
          <div className="card" key={d.key}>
            <h3>{d.label} · {d.game}</h3>
            <div style={{fontSize:12,color:"var(--sub)",marginBottom:10}}>
              임원 참여 시 해당 팀의 {d.label} 획득 점수가 <b style={{color:"var(--gold)"}}>×{EXEC_MULTIPLIER}</b>로 계산됩니다.
            </div>
            <div>
              <label style={{fontSize:11,color:"var(--sub)",fontWeight:700}}>임원 참여 팀 선택</label>
              <div style={{display:"flex",gap:10,flexWrap:"wrap",marginTop:6}}>
                {state.teams.map(t=>(
                  <label className="exec-toggle" key={t.id}>
                    <input type="checkbox" checked={cfg.execTeams.includes(t.id)} onChange={()=>toggleExec(d.key, t.id)} />
                    {t.name}{cfg.execTeams.includes(t.id) && <span className="exec-badge" style={{marginLeft:4}}>×{EXEC_MULTIPLIER}</span>}
                  </label>
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ============================== admin: rules view tab ============================== */
function RulesTab({ state }){
  return (
    <div className="card regulation">
      <h3>배점 규정</h3>
      <table className="table">
        <thead><tr><th>1등</th><th>2등</th><th>3등</th><th>4~6등</th><th>7~9등</th></tr></thead>
        <tbody><tr><td>5점</td><td>4점</td><td>3점</td><td>2점</td><td>1점</td></tr></tbody>
      </table>
      <div style={{fontSize:13,color:"var(--sub)",marginTop:6}}>
        모든 Day(Day1~4) 공통 배점입니다. 토너먼트(Day1·2·4)는 3·4위 결정전이 없어
        준결승에서 탈락한 두 팀이 공동 3등(3점)으로, 그 이전 라운드 탈락 팀들은
        각각 4~6등(2점) · 7~9등(1점) 구간으로 계산됩니다.
      </div>
      <div style={{fontSize:13,color:"var(--sub)",marginTop:6}}>
        <b style={{color:"var(--gold)"}}>종합 순위 동점자 처리</b>: 종합 점수가 같으면 게임별 1등(우승/1위) 횟수가 많은 팀이 앞섭니다.
      </div>

      <h4>Day별 임원 참여 가중치</h4>
      {DAYS.map(d=>{
        const cfg = state.scoring[d.key];
        return (
          <div key={d.key} style={{fontSize:13,color:"var(--sub)",marginTop:4}}>
            <b style={{color:"var(--text)"}}>{d.label} · {d.game}</b> — 임원 참여 시 획득 점수 × {EXEC_MULTIPLIER} 적용 ·
            대상팀: {cfg.execTeams.length ? cfg.execTeams.map(id=>state.teams.find(t=>t.id===id)?.name).join(", ") : "없음"}
          </div>
        );
      })}
    </div>
  );
}

/* ============================== admin: control tab ============================== */
function ControlTab({ state, update }){
  const setActive = (key)=> update(s=>{ s.display.activeDayKey = key; return s; });
  const resetAll = ()=>{
    if(!window.confirm("이 브라우저에 저장된 모든 데이터(팀 정보, 대진표, 순위, 배점 설정 등)를 지우고 초기 상태로 되돌립니다. 계속할까요?")) return;
    try{ localStorage.removeItem(STORAGE_KEY); }catch(e){}
    window.location.reload();
  };
  const exportExcel = ()=>{
    try{
      const wb = buildResultWorkbook(state);
      const stamp = new Date().toISOString().slice(0,10);
      XLSX.writeFile(wb, `썸머탈출페스티벌_결과_${stamp}.xlsx`);
    }catch(e){
      window.alert("엑셀 생성에 실패했습니다: " + (e && e.message ? e.message : e));
    }
  };
  const activeDay = dayInfo(state.display.activeDayKey);
  const exportDayExcel = ()=>{
    try{
      const wb = buildDayResultWorkbook(state, state.display.activeDayKey);
      const stamp = new Date().toISOString().slice(0,10);
      XLSX.writeFile(wb, `썸머탈출페스티벌_${activeDay.label}_결과_${stamp}.xlsx`);
    }catch(e){
      window.alert("엑셀 생성에 실패했습니다: " + (e && e.message ? e.message : e));
    }
  };
  const downloadPosterPrompt = ()=>{
    try{
      const text = buildPosterPrompt(state, state.display.activeDayKey);
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `썸머탈출페스티벌_${activeDay.label}_포스터프롬프트.txt`;
      a.click();
      URL.revokeObjectURL(url);
    }catch(e){
      window.alert("프롬프트 생성에 실패했습니다: " + (e && e.message ? e.message : e));
    }
  };
  return (
    <div className="card">
      <h3>송출 화면 제어</h3>
      <p style={{color:"var(--sub)",fontSize:13}}>TV/플립 화면에 표시할 오늘의 경기를 선택하세요.</p>
      <div style={{display:"flex",gap:10,flexWrap:"wrap"}}>
        {DAYS.map(d=>(
          <button key={d.key} className={"tab-btn" + (state.display.activeDayKey===d.key?" active":"")} onClick={()=>setActive(d.key)}>
            {d.label} · {d.game}
          </button>
        ))}
      </div>
      <div style={{marginTop:18,paddingTop:16,borderTop:"1px dashed var(--line)"}}>
        <p style={{color:"var(--sub)",fontSize:12,marginBottom:8}}>
          선택된 Day({activeDay.label} · {activeDay.game})의 결과로 포스터 제작용 프롬프트와 엑셀을 내려받습니다.
          프롬프트 파일을 열어 이미지 생성 AI에 붙여넣고, 엑셀 파일을 함께 첨부하세요.
        </p>
        <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
          <button className="small-btn" onClick={downloadPosterPrompt}>🖼️ 포스터 프롬프트 다운로드 (.txt)</button>
          <button className="small-btn" onClick={exportDayExcel}>📊 {activeDay.label} 결과 엑셀 다운로드</button>
        </div>
      </div>
      <div style={{marginTop:18,paddingTop:16,borderTop:"1px dashed var(--line)"}}>
        <p style={{color:"var(--sub)",fontSize:12,marginBottom:8}}>
          현재까지 입력된 종합 순위와 Day별 대진표/순위 결과를 엑셀 파일(.xlsx)로 내려받습니다.
        </p>
        <button className="small-btn" onClick={exportExcel}>
          📊 최종 결과 엑셀 다운로드
        </button>
      </div>
      <div style={{marginTop:18,paddingTop:16,borderTop:"1px dashed var(--line)"}}>
        <p style={{color:"var(--sub)",fontSize:12,marginBottom:8}}>
          이 노트북 브라우저에 예전 버전으로 저장된 데이터가 남아있으면(예: 업데이트 전 대진표) 새 파일을 열어도 그 저장된 내용이 계속 보입니다.
          최신 상태로 완전히 초기화하려면 아래 버튼을 사용하세요.
        </p>
        <button className="small-btn" style={{borderColor:"var(--red)",color:"var(--red)"}} onClick={resetAll}>
          ⚠️ 전체 초기화 (기본값으로 되돌리기)
        </button>
      </div>
    </div>
  );
}

/* ============================== admin root ============================== */
function AdminView({ state, update }){
  const [tab, setTab] = useState("control");
  const teamsById = useMemo(()=>Object.fromEntries(state.teams.map(t=>[t.id,t])),[state.teams]);
  const tabs = [
    {key:"control", label:"화면 제어"},
    {key:"teams", label:"팀 관리"},
    {key:"day1", label:"DAY1 젠가"},
    {key:"day2", label:"DAY2 카트라이더"},
    {key:"day3", label:"DAY3 팀빌딩"},
    {key:"day4", label:"DAY4 컬링"},
    {key:"scoring", label:"배점 설정"},
    {key:"rules", label:"배점 규정 보기"},
  ];
  return (
    <div className="admin-wrap">
      <div className="topbar" style={{margin:"-0px -28px 20px"}}>
        <div className="brand"><span className="emoji">🛠️</span><h1>관리자 모드</h1></div>
        <div className="sub" style={{color:"var(--sub)"}}>이 노트북 화면에서만 입력됩니다 (오프라인, 이 브라우저에 저장)</div>
      </div>
      <div className="tabs">
        {tabs.map(t=>(
          <button key={t.key} className={"tab-btn" + (tab===t.key?" active":"")} onClick={()=>setTab(t.key)}>{t.label}</button>
        ))}
      </div>
      {tab==="control" && <ControlTab state={state} update={update} />}
      {tab==="teams" && <TeamsTab state={state} update={update} />}
      {tab==="day1" && <DayTab dayKey="day1" state={state} update={update} teamsById={teamsById} />}
      {tab==="day2" && <DayTab dayKey="day2" state={state} update={update} teamsById={teamsById} />}
      {tab==="day3" && <DayTab dayKey="day3" state={state} update={update} teamsById={teamsById} />}
      {tab==="day4" && <DayTab dayKey="day4" state={state} update={update} teamsById={teamsById} />}
      {tab==="scoring" && <ScoringTab state={state} update={update} />}
      {tab==="rules" && <RulesTab state={state} />}
    </div>
  );
}

/* ============================== app root ============================== */
function App(){
  const { state, update, saveError } = useLocalState();
  const [mode, setMode] = useState(()=>{
    try{ return new URLSearchParams(window.location.search).get("admin")==="1" ? "admin" : "display"; }
    catch(e){ return "display"; }
  });

  const heroGames = (mode==="display" && state) ? (DAY_ILLUSTRATIONS[state.display.activeDayKey] || []) : [];
  // bracket days now use the full canvas bottom-to-top, so the illustration
  // moves up beside the final-round box (a narrower gap) instead of sitting
  // at the bottom, and needs to be a bit smaller to fit there
  const heroPos = (state && dayInfo(state.display.activeDayKey).format==="ranking") ? "bottom" : "top";
  const heroSize = heroGames.length>1 ? 190 : (heroPos==="top" ? 260 : 280);

  return (
    <div className="app">
      <div className="fest-bg" aria-hidden="true">
        <span className="fest-sun">☀️</span>
        <span className="fest-cloud fest-cloud-a">☁️</span>
        <span className="fest-cloud fest-cloud-b">☁️</span>
        <span className="fest-palm fest-palm-l">🌴</span>
        <span className="fest-palm fest-palm-r">🌴</span>

        {heroGames.length>0 && <div className={"fest-hero fest-hero-" + heroPos}>
          {heroGames.map((g,i)=>{
            const Illust = ILLUSTRATIONS[g.id];
            const layout = heroGames.length>1
              ? [{ rot:-6, y:22, ml:0 }, { rot:9, y:-30, ml:-heroSize*0.16 }][i]
              : { rot:-7, y:0, ml:0 };
            return (
              <div className="fest-hero-item" key={g.id} title={g.label}
                style={{transform:`rotate(${layout.rot}deg) translateY(${layout.y}px)`, marginLeft:layout.ml, zIndex:i}}>
                <Illust size={heroSize} />
              </div>
            );
          })}
        </div>}

        <svg className="fest-wave" viewBox="0 0 1440 120" preserveAspectRatio="none">
          <path d="M0,45 C240,95 480,5 720,45 C960,85 1200,15 1440,55 L1440,120 L0,120 Z" fill="rgba(234,247,255,0.55)"/>
          <path d="M0,75 C240,115 480,45 720,75 C960,105 1200,55 1440,85 L1440,120 L0,120 Z" fill="rgba(234,247,255,0.9)"/>
        </svg>
      </div>
      <div className="app-content">
        {saveError && <div className="banner">⚠️ {saveError}</div>}
        {mode==="display" ? <DisplayView state={state} update={update} /> : <AdminView state={state} update={update} />}
        <button className="floating-toggle" onClick={()=>setMode(m=>m==="admin"?"display":"admin")} title="관리자 모드 전환 (여기를 눌러 전환)">
          {mode==="admin" ? "📺" : "⚙️"}
        </button>
      </div>
    </div>
  );
}

/* ============================== crash guard ============================== */
/* If anything throws during render — a bad save, a future data-shape
   change, a bug — this catches it and shows a recoverable screen with a
   reset button and the actual error text, instead of a blank page with
   nothing to click (which is what a render crash looks like otherwise:
   the .app background never mounts, so only the plain body color shows). */
class ErrorBoundary extends React.Component {
  constructor(props){ super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error){ return { error }; }
  componentDidCatch(error, info){ try{ console.error("render crashed:", error, info); }catch(e){} }
  reset = () => {
    if(!window.confirm("이 브라우저에 저장된 모든 데이터를 지우고 초기 상태로 되돌립니다. 계속할까요?")) return;
    try{ localStorage.removeItem(STORAGE_KEY); }catch(e){}
    window.location.reload();
  };
  render(){
    if(!this.state.error) return this.props.children;
    return (
      <div style={{minHeight:"100vh",display:"flex",flexDirection:"column",alignItems:"center",
        justifyContent:"center",gap:16,padding:24,textAlign:"center",background:"#0b1e3d",color:"#eaf4ff",
        fontFamily:"'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif"}}>
        <div style={{fontSize:40}}>⚠️</div>
        <h2 style={{margin:0}}>문제가 발생했습니다</h2>
        <p style={{color:"#9db4d9",maxWidth:480,lineHeight:1.6}}>
          저장된 데이터에 문제가 있어 화면을 그릴 수 없습니다.<br/>
          아래 버튼으로 데이터를 초기화하면 정상적으로 다시 시작할 수 있습니다.
        </p>
        <button onClick={this.reset} style={{border:"1px solid #ff5c5c",color:"#ff5c5c",background:"transparent",
          borderRadius:8,padding:"10px 18px",fontWeight:700,cursor:"pointer",fontSize:14}}>
          ⚠️ 전체 초기화하고 다시 시작
        </button>
        <pre style={{color:"#5a6f8f",fontSize:11,maxWidth:640,overflow:"auto",whiteSpace:"pre-wrap",textAlign:"left"}}>
          {String((this.state.error && this.state.error.stack) || this.state.error)}
        </pre>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(<ErrorBoundary><App /></ErrorBoundary>);
