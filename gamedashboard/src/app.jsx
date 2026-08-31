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
function buildBracket(teamIds){
  const n = teamIds.length;
  let size = 1; while(size<n) size*=2;
  const totalR1 = size/2;
  const byes = size - n;
  const realMatches = totalR1 - byes;
  let ti = 0;
  const round1 = [];
  for(let i=0;i<realMatches;i++){
    round1.push({ id:`r0m${i}`, teamAId:teamIds[ti++], teamBId:teamIds[ti++], winnerId:null, meta:{} });
  }
  for(let i=0;i<byes;i++){
    const team = teamIds[ti++];
    round1.push({ id:`r0m${realMatches+i}`, teamAId:team, teamBId:null, winnerId:team, meta:{auto:true} });
  }
  const rounds = [round1];
  let prevCount = totalR1, r = 1;
  while(prevCount>1){
    const cnt = prevCount/2;
    const round = [];
    for(let i=0;i<cnt;i++){
      round.push({ id:`r${r}m${i}`, teamASource:{round:r-1,match:i*2}, teamBSource:{round:r-1,match:i*2+1}, winnerId:null, meta:{} });
    }
    rounds.push(round);
    prevCount = cnt; r++;
  }
  return { rounds };
}
function getWinnerOf(bracket, round, idx){
  const m = bracket.rounds[round] && bracket.rounds[round][idx];
  return m ? (m.winnerId || null) : null;
}
function getSlotTeam(bracket, round, idx, side){
  const m = bracket.rounds[round][idx];
  if(side==="A"){
    if(m.teamAId) return m.teamAId;
    if(m.teamASource) return getWinnerOf(bracket, m.teamASource.round, m.teamASource.match);
    return null;
  } else {
    if(m.teamBId) return m.teamBId;
    if(m.teamBSource) return getWinnerOf(bracket, m.teamBSource.round, m.teamBSource.match);
    return null;
  }
}
function clearDownstream(bracket, round, idx){
  const next = round+1;
  if(!bracket.rounds[next]) return;
  bracket.rounds[next].forEach((m,i)=>{
    const depA = m.teamASource && m.teamASource.round===round && m.teamASource.match===idx;
    const depB = m.teamBSource && m.teamBSource.round===round && m.teamBSource.match===idx;
    if((depA||depB) && m.winnerId){
      m.winnerId = null; m.meta = {};
      clearDownstream(bracket, next, i);
    }
  });
}
/* The order buildBracket() consumed to fill round-1 slots (real matches'
   A,B pairs first, then each bye's single team) — reconstructing it lets
   the seeding editor show/re-permute "who sits where" per day. */
function getRound1TeamOrder(bracket){
  const order = [];
  bracket.rounds[0].forEach(m=>{
    order.push(m.teamAId);
    if(m.teamBId) order.push(m.teamBId);
  });
  return order;
}
function bracketHasResults(bracket){
  return bracket.rounds.some(round=>round.some(m=>{
    const hasWinner = m.winnerId && !(m.meta && m.meta.auto);
    const hasMeta = m.meta && (m.meta.firstMoverId || m.meta.setScore);
    return hasWinner || hasMeta;
  }));
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
      day3:{ mode:"auto", entries: teamIds.map(id=>({teamId:id, timeSec:null, manualRank:null})) },
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
  if(day3.mode==="manual"){
    return entries.map((e,i)=>({ teamId:e.teamId, rank: i+1, timeSec:e.timeSec }));
  }
  const withTime = entries.filter(e=>e.timeSec!=null).sort((a,b)=>a.timeSec-b.timeSec);
  const withoutTime = entries.filter(e=>e.timeSec==null);
  const ranked = withTime.map((e,i)=>({teamId:e.teamId, rank:i+1, timeSec:e.timeSec}));
  withoutTime.forEach(e=>ranked.push({teamId:e.teamId, rank:null, timeSec:null}));
  return ranked;
}
/* Bracket placement has no 3rd-place playoff, so it collapses to 5 tiers
   by the round a team was eliminated in (or champion, if never eliminated):
   final winner / final loser / lost semifinal / lost the round before /
   lost the round before that — TIER_POINTS' 5 values in order. Only
   decided matches award points, so in-progress days show partial scores. */
function computeBracketScores(dayState){
  const scores = {};
  const bracket = dayState.bracket;
  const numRounds = bracket.rounds.length;
  bracket.rounds.forEach((round, r)=>{
    round.forEach((m,i)=>{
      if(!m.winnerId) return;
      const isBye = m.meta && m.meta.auto;
      if(isBye) return; // a walkover eliminates nobody
      const aId = getSlotTeam(bracket, r, i, "A");
      const bId = getSlotTeam(bracket, r, i, "B");
      const loserId = m.winnerId===aId ? bId : aId;
      if(r === numRounds-1){
        scores[m.winnerId] = TIER_POINTS[0];
        if(loserId) scores[loserId] = TIER_POINTS[1];
      } else if(loserId){
        const tier = Math.min(numRounds - r, TIER_POINTS.length-1);
        scores[loserId] = TIER_POINTS[tier];
      }
    });
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
  const final = bracket.rounds[bracket.rounds.length-1][0];
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
function loadInitialState(){
  try{
    const raw = localStorage.getItem(STORAGE_KEY);
    if(raw) return JSON.parse(raw);
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
function TeamChip({team, size, exec}){
  if(!team) return <span className="slot tbd">TBD</span>;
  return (
    <span className="team-chip">
      {team.image
        ? <img className={"team-avatar" + (size==="big"?" big":"")} src={team.image} />
        : <span className="dot" style={{width:size==="big"?52:22,height:size==="big"?52:22,borderRadius:"50%",background:team.color,flex:"none",border:"2px solid rgba(255,255,255,.25)"}}></span>}
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
  return (
    <svg width={size} height={size} viewBox="0 0 120 120">
      <g strokeLinecap="round" opacity=".85">
        <line x1="-4" y1="42" x2="12" y2="42" stroke="#eaf7ff" strokeWidth="4"/>
        <line x1="-4" y1="58" x2="16" y2="58" stroke="#eaf7ff" strokeWidth="4"/>
        <line x1="-4" y1="74" x2="10" y2="74" stroke="#eaf7ff" strokeWidth="4"/>
      </g>
      <path d="M15,80 Q15,63 36,61 L74,59 Q97,59 102,77 L102,86 Q102,91 97,91 L20,91 Q15,91 15,86 Z"
        fill="#ff7a29" stroke="#7a3d00" strokeWidth="2" strokeLinejoin="round"/>
      <rect x="90" y="55" width="12" height="12" rx="3" fill="#ff5c5c" stroke="#7a1010" strokeWidth="1.5"/>
      <circle cx="64" cy="50" r="11" fill="#26e0d9" stroke="#0b3a63" strokeWidth="2"/>
      <path d="M56,48 Q64,44 72,48" stroke="#0b3a63" strokeWidth="2" fill="none" strokeLinecap="round"/>
      <circle cx="33" cy="92" r="13" fill="#22314f" stroke="#0b3a63" strokeWidth="1.5"/>
      <circle cx="33" cy="92" r="5" fill="#cfd8e6"/>
      <circle cx="86" cy="92" r="13" fill="#22314f" stroke="#0b3a63" strokeWidth="1.5"/>
      <circle cx="86" cy="92" r="5" fill="#cfd8e6"/>
    </svg>
  );
}
function GonggiIllustration({size}){
  return (
    <svg width={size} height={size} viewBox="0 0 120 120">
      <g stroke="#0b3a63" strokeWidth="1.5">
        <ellipse cx="35" cy="60" rx="12" ry="9" fill="#ff5c8a"/>
        <ellipse cx="82" cy="55" rx="11" ry="9" fill="#7ee787"/>
        <ellipse cx="45" cy="75" rx="14" ry="11" fill="#ff9f45"/>
        <ellipse cx="70" cy="70" rx="13" ry="10" fill="#26e0d9"/>
        <ellipse cx="58" cy="85" rx="13" ry="10" fill="#ffd166"/>
      </g>
      <g fill="#fff8ea" opacity=".9">
        <path d="M92,28 l3,7 l7,3 l-7,3 l-3,7 l-3,-7 l-7,-3 l7,-3 Z"/>
        <path d="M22,34 l2,5 l5,2 l-5,2 l-2,5 l-2,-5 l-5,-2 l5,-2 Z"/>
      </g>
    </svg>
  );
}
function JumpRopeIllustration({size}){
  return (
    <svg width={size} height={size} viewBox="0 0 120 120">
      <path d="M10,86 Q60,8 110,86" stroke="#e0542a" strokeWidth="7" fill="none" strokeLinecap="round"/>
      <g strokeLinecap="round">
        <circle cx="40" cy="55" r="10" fill="#ffd166" stroke="#0b3a63" strokeWidth="2"/>
        <path d="M40,65 L33,90 M40,65 L47,90 M31,71 L20,61 M49,71 L60,61" stroke="#0b3a63" strokeWidth="4.5" fill="none"/>
        <circle cx="80" cy="48" r="10" fill="#ff5c8a" stroke="#0b3a63" strokeWidth="2"/>
        <path d="M80,58 L73,86 M80,58 L87,86 M71,64 L60,54 M89,64 L100,54" stroke="#0b3a63" strokeWidth="4.5" fill="none"/>
      </g>
    </svg>
  );
}
function CurlingIllustration({size}){
  return <img src={GAME_IMAGES.curling} alt="" style={{width:size, height:"auto"}} />;
}
const ILLUSTRATIONS = {
  jenga: JengaIllustration, kart: KartIllustration, gonggi: GonggiIllustration,
  jumprope: JumpRopeIllustration, curling: CurlingIllustration,
};
/* which illustration(s) represent each day's game on the display screen */
const DAY_ILLUSTRATIONS = {
  day1: [{ id:"jenga", label:"자이언트 젠가" }],
  day2: [{ id:"kart", label:"카트라이더" }],
  day3: [{ id:"jumprope", label:"단체줄넘기" }, { id:"gonggi", label:"공기놀이" }],
  day4: [{ id:"curling", label:"컬링" }],
};

/* ============================== bracket board ============================== */
/* Rounds stack bottom-to-top: round 1 at the bottom, the final at the top. */
function BracketBoard({ bracket, teamsById, editable, onSetWinner, renderExtra, execTeamIds }){
  const containerRef = useRef(null);
  const matchRefs = useRef({});
  const [lines, setLines] = useState([]);
  const setRef = (r,i) => (el)=>{ matchRefs.current[`${r}-${i}`] = el; };
  const isExec = (id)=> !!(id && execTeamIds && execTeamIds.includes(id));

  const recompute = useCallback(()=>{
    const cont = containerRef.current;
    if(!cont) return;
    const cRect = cont.getBoundingClientRect();
    const newLines = [];
    bracket.rounds.forEach((round, r)=>{
      if(r === bracket.rounds.length-1) return;
      const nextRound = bracket.rounds[r+1];
      round.forEach((m, i)=>{
        const targetIdx = nextRound.findIndex(nm =>
          (nm.teamASource && nm.teamASource.round===r && nm.teamASource.match===i) ||
          (nm.teamBSource && nm.teamBSource.round===r && nm.teamBSource.match===i));
        if(targetIdx<0) return;
        const srcEl = matchRefs.current[`${r}-${i}`];
        const tgtEl = matchRefs.current[`${r+1}-${targetIdx}`];
        if(!srcEl || !tgtEl) return;
        const sr = srcEl.getBoundingClientRect(), tr = tgtEl.getBoundingClientRect();
        // source round sits below target round: line exits the top of the
        // source box and enters the bottom of the target box.
        newLines.push({
          x1: sr.left + sr.width/2 - cRect.left, y1: sr.top - cRect.top,
          x2: tr.left + tr.width/2 - cRect.left, y2: tr.bottom - cRect.top,
          decided: !!round[i].winnerId,
        });
      });
    });
    setLines(newLines);
  },[bracket]);

  useLayoutEffect(()=>{
    recompute();
    const onResize = ()=>recompute();
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(()=>recompute());
    if(containerRef.current) ro.observe(containerRef.current);
    return ()=>{ window.removeEventListener("resize", onResize); ro.disconnect(); };
  },[recompute]);

  return (
    <div className="bracket-scroll">
      <div className="bracket" ref={containerRef}>
        <svg className="connectors">
          {lines.map((l,idx)=>{
            const midY = (l.y1+l.y2)/2;
            const d = `M ${l.x1} ${l.y1} V ${midY} H ${l.x2} V ${l.y2}`;
            return <path key={idx} d={d} className={l.decided?"decided":""} />;
          })}
        </svg>
        {bracket.rounds.map((round, r)=>(
          <div className="round-block" key={r}>
            <div className="round-label">{r===bracket.rounds.length-1 ? "결승" : `${r+1}라운드`}</div>
            <div className="round-row">
              {round.map((m, i)=>{
                const aId = getSlotTeam(bracket, r, i, "A");
                const bId = getSlotTeam(bracket, r, i, "B");
                const aTeam = aId ? teamsById[aId] : null;
                const bTeam = bId ? teamsById[bId] : null;
                const isBye = m.meta && m.meta.auto;
                const canPick = editable && !isBye && aTeam && bTeam;
                const rowClass = (id)=> id && m.winnerId ? (id===m.winnerId?"slot winner":"slot loser") : "slot";
                const pick = (teamId)=>{
                  if(!canPick) return;
                  const changing = !!m.winnerId && m.winnerId!==teamId;
                  if(changing && !window.confirm("정말 변경하시겠습니까? 다음 라운드에 반영된 결과도 함께 초기화됩니다.")) return;
                  onSetWinner(r, i, teamId);
                };
                return (
                  <div className={"match" + (m.winnerId?" decided":"")} key={m.id} ref={setRef(r,i)}>
                    {canPick ? (
                      <button className="slot-btn" onClick={()=>pick(aId)}>
                        <span className={rowClass(aId)} style={{width:"100%"}}><TeamChip team={aTeam} exec={isExec(aId)} /></span>
                      </button>
                    ) : (
                      <div className={rowClass(aId) + (!aTeam?" tbd":"")}><TeamChip team={aTeam} exec={isExec(aId)} /></div>
                    )}
                    {canPick ? (
                      <button className="slot-btn" onClick={()=>pick(bId)}>
                        <span className={rowClass(bId)} style={{width:"100%"}}><TeamChip team={bTeam} exec={isExec(bId)} /></span>
                      </button>
                    ) : (
                      <div className={rowClass(bId) + (!bTeam?" tbd":"")}><TeamChip team={bTeam} exec={isExec(bId)} /></div>
                    )}
                    {m.meta && m.meta.firstMoverId && teamsById[m.meta.firstMoverId] &&
                      <div className="match-meta">{m.meta.firstMoverLabel||"선공"}: <b>{teamsById[m.meta.firstMoverId].name}</b></div>}
                    {m.meta && m.meta.setScore &&
                      <div className="match-meta">세트 스코어: <b>{m.meta.setScore}</b></div>}
                    {renderExtra && aTeam && bTeam && !isBye && renderExtra(m, r, i)}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============================== ranking board ============================== */
function RankingBoard({ day3, teamsById, execTeamIds }){
  const ranked = getRankedEntries(day3).slice().sort((a,b)=>{
    if(a.rank==null) return 1; if(b.rank==null) return -1; return a.rank-b.rank;
  });
  return (
    <div className="ranking-list">
      {ranked.map((e,idx)=>{
        const t = teamsById[e.teamId];
        const rc = e.rank===1?"r1":e.rank===2?"r2":e.rank===3?"r3":"";
        return (
          <div className={"rank-row " + rc} key={e.teamId}>
            <div className="rank-num">{e.rank || "-"}</div>
            <TeamChip team={t} exec={execTeamIds && execTeamIds.includes(e.teamId)} />
            <div className="rank-time">{e.timeSec!=null ? fmtTime(e.timeSec) : "미기록"}</div>
          </div>
        );
      })}
    </div>
  );
}

/* ============================== display view ============================== */
function DisplayView({ state }){
  const teamsById = useMemo(()=>Object.fromEntries(state.teams.map(t=>[t.id,t])),[state.teams]);
  const activeKey = state.display.activeDayKey;
  const info = dayInfo(activeKey);
  const execTeamIds = state.scoring[activeKey].execTeams;
  const overall = useMemo(()=>computeOverall(state),[state]);

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
        <div className="today-panel">
          <div className="panel-title"><span className="bar"></span>오늘의 경기 현황 · {info.game}</div>
          {info.format==="bracket"
            ? <BracketBoard bracket={state.days[activeKey].bracket} teamsById={teamsById} editable={false} onSetWinner={()=>{}} execTeamIds={execTeamIds} />
            : <RankingBoard day3={state.days.day3} teamsById={teamsById} execTeamIds={execTeamIds} />}
        </div>
        <div className="overall-panel">
          <div className="panel-title"><span className="bar" style={{background:"var(--cyan)"}}></span>종합 순위 TOP 9</div>
          <div className="top9">
            {overall.map((t,idx)=>(
              <div className={"top9-row" + (idx<3?" top3":"")} key={t.id}>
                <div className="top9-rank">{idx+1}</div>
                <TeamChip team={t} exec={execTeamIds.includes(t.id)} />
                {t.firstPlaceCount>0 && <span className="gold-count" title="게임별 1등 횟수 (동점 시 우선순위)">🥇×{t.firstPlaceCount}</span>}
                <div className="top9-score">{t.total}<span style={{fontSize:11,color:"var(--sub)",fontWeight:600}}> pt</span></div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ============================== admin: day1 first-mover picker ============================== */
/* Admin just picks who goes first directly — the timer itself is run outside the app. */
function FirstMoverPicker({ match, onUpdate, teamsById, aId, bId }){
  const current = match.meta && match.meta.firstMoverId;
  const pick = (teamId)=> onUpdate({ firstMoverId: teamId, firstMoverLabel: "선공" });
  return (
    <div className="stopwatch-box" onClick={e=>e.stopPropagation()}>
      <div style={{fontSize:11,color:"var(--sub)",fontWeight:700,marginBottom:6}}>선공 팀 선택</div>
      <div className="stopwatch-row">
        <button className={"small-btn" + (current===aId?" primary":"")} onClick={()=>pick(aId)}>
          {teamsById[aId] && teamsById[aId].name} 선공
        </button>
        <button className={"small-btn" + (current===bId?" primary":"")} onClick={()=>pick(bId)}>
          {teamsById[bId] && teamsById[bId].name} 선공
        </button>
      </div>
    </div>
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
  const execTeamIds = state.scoring[dayKey].execTeams;

  const setWinner = (r,i,teamId)=>{
    update(s=>{
      const b = s.days[dayKey].bracket;
      const m = b.rounds[r][i];
      if(m.winnerId===teamId) return null;
      m.winnerId = teamId;
      clearDownstream(b, r, i);
      return s;
    });
  };
  const setMeta = (r,i,patch)=>{
    update(s=>{
      const m = s.days[dayKey].bracket.rounds[r][i];
      m.meta = { ...m.meta, ...patch };
      return s;
    });
  };

  if(info.format==="bracket"){
    return (
      <div>
        <SeedingEditor dayKey={dayKey} bracket={dayState.bracket} teams={state.teams} update={update} />
        <div className="card">
        <div className="day-header">
          <div className="title">{info.label} · {info.game}</div>
          <div className="sub">{info.date} · {info.place} · {info.unit}</div>
        </div>
        <BracketBoard
          bracket={dayState.bracket}
          teamsById={teamsById}
          editable={true}
          execTeamIds={execTeamIds}
          onSetWinner={(r,i,teamId)=>setWinner(r,i,teamId)}
          renderExtra={(m,r,i)=>{
            const aId = getSlotTeam(dayState.bracket, r, i, "A");
            const bId = getSlotTeam(dayState.bracket, r, i, "B");
            return (
              <div>
                {info.hasFirstMover &&
                  <FirstMoverPicker match={m} teamsById={teamsById} aId={aId} bId={bId}
                    onUpdate={(patch)=>setMeta(r,i,patch)} />}
                {info.hasSetScore &&
                  <div className="stopwatch-box" onClick={e=>e.stopPropagation()}>
                    <div className="field" style={{marginBottom:0}}>
                      <label>세트 스코어 (예: 2:1)</label>
                      <input type="text" defaultValue={(m.meta&&m.meta.setScore)||""} placeholder="2:1"
                        onBlur={(e)=>setMeta(r,i,{setScore:e.target.value})} />
                    </div>
                  </div>}
              </div>
            );
          }}
        />
        </div>
      </div>
    );
  }

  // day3 ranking editor
  const entries = dayState.entries;
  const toggleMode = (mode)=> update(s=>{ s.days.day3.mode = mode; return s; });
  const setTime = (teamId, val)=> update(s=>{
    const e = s.days.day3.entries.find(x=>x.teamId===teamId);
    e.timeSec = parseTimeInput(val);
    return s;
  });
  const move = (idx, dir)=> update(s=>{
    const arr = s.days.day3.entries;
    const j = idx+dir;
    if(j<0||j>=arr.length) return null;
    const tmp = arr[idx]; arr[idx]=arr[j]; arr[j]=tmp;
    return s;
  });
  const ranked = getRankedEntries(dayState);
  const rankOf = Object.fromEntries(ranked.map(e=>[e.teamId,e.rank]));

  return (
    <div className="card">
      <div className="day-header">
        <div className="title">{info.label} · {info.game}</div>
        <div className="sub">{info.date} · {info.place} · {info.unit}</div>
      </div>
      <div style={{marginBottom:12,display:"flex",gap:8}}>
        <button className={"small-btn" + (dayState.mode!=="manual"?" primary":"")} onClick={()=>toggleMode("auto")}>자동 정렬 (완료 시간 기준)</button>
        <button className={"small-btn" + (dayState.mode==="manual"?" primary":"")} onClick={()=>toggleMode("manual")}>수동 순서 지정</button>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:8}}>
        {entries.map((e,idx)=>{
          const t = teamsById[e.teamId];
          return (
            <div className="rank-edit-row" key={e.teamId}>
              <div style={{width:30,textAlign:"center",fontWeight:900,color:"var(--gold)"}}>{rankOf[e.teamId]||"-"}</div>
              <TeamChip team={t} exec={execTeamIds.includes(e.teamId)} />
              {dayState.mode!=="manual"
                ? <input type="text" style={{marginLeft:"auto",width:100}} placeholder="mm:ss"
                    defaultValue={e.timeSec!=null?fmtTime(e.timeSec):""}
                    onBlur={(ev)=>setTime(e.teamId, ev.target.value)} />
                : <div style={{marginLeft:"auto",display:"flex",gap:6}}>
                    <button className="small-btn" onClick={()=>move(idx,-1)}>↑</button>
                    <button className="small-btn" onClick={()=>move(idx,1)}>↓</button>
                  </div>}
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
  const heroSize = heroGames.length>1 ? 190 : 280;

  return (
    <div className="app">
      <div className="fest-bg" aria-hidden="true">
        <span className="fest-sun">☀️</span>
        <span className="fest-cloud fest-cloud-a">☁️</span>
        <span className="fest-cloud fest-cloud-b">☁️</span>
        <span className="fest-palm fest-palm-l">🌴</span>
        <span className="fest-palm fest-palm-r">🌴</span>

        {heroGames.length>0 && <div className="fest-hero">
          {heroGames.map(g=>{
            const Illust = ILLUSTRATIONS[g.id];
            return (
              <div className="fest-hero-item" key={g.id}>
                <Illust size={heroSize} />
                <span className="label">{g.label}</span>
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
        {mode==="display" ? <DisplayView state={state} /> : <AdminView state={state} update={update} />}
        <button className="floating-toggle" onClick={()=>setMode(m=>m==="admin"?"display":"admin")} title="관리자 모드 전환 (여기를 눌러 전환)">
          {mode==="admin" ? "📺" : "⚙️"}
        </button>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
