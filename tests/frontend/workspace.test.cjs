// No framework or DOM package: exercise production rendering, guards and request races.
const test = require("node:test");
const assert = require("node:assert/strict");
const ui = require("../../src/casefile_ai/interfaces/web/workspace.js");
function element(tagName="DIV", dataset={}) {
  return {tagName,dataset,disabled:false,textContent:"",innerHTML:"",value:"",
    hasAttribute(name){return name==="data-case-control" && this.dataset.caseControl==="true";},
    insertAdjacentHTML(_,html){this.innerHTML+=html;},replaceChildren(){this.innerHTML="";this.options=[];},append(item){this.options.push(item);},options:[]};
}
const nodes = new Map();
const buttons = [element("BUTTON",{operation:"ask"}),element("BUTTON",{operation:"index"}),element("INPUT"),element("BUTTON",{blocked:"true"})];
const message = element();
global.document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},
  createElement(tag){return element(tag);},
  querySelectorAll(selector){return selector.includes("data-operation-message") ? [message] : buttons;}};
const system={database:{reachable:true,schema_status:"current"},data_directory:{writable:true},embedding:{available:true,loaded:false},ocr:{dependency_available:true,model_available:true},qa_provider:{configured:true,enabled:true,connection_verified:false},logs:{file_logging_available:true}};
function reset(){ui.setState({current:"A",epoch:1});ui.pending.clear();ui.loads.clear();nodes.clear();buttons.forEach(b=>{b.disabled=false;b.textContent="操作";delete b.dataset.idleLabel;});global.fetch=async()=>({ok:true,json:async()=>system});}
function deferred(){let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};}

test("provider errors show Chinese safe fields without raw body, prompt or secrets",()=>{
  for(const category of ["authentication_error","permission_error","rate_limited","timeout","unavailable","connection_error","invalid_response","model_not_configured","model_disabled","unknown_provider_error"]){
    const result=ui.errorInfo({detail:"RAW_SECRET",error:{category,http_status:401,diagnostic_id:"00000000-0000-0000-0000-000000000001",body:"RAW_SECRET"}},503);
    assert.match(result.message,/[\u4e00-\u9fa5]/);assert.equal(result.category,category);assert.equal(result.httpStatus,401);assert.doesNotMatch(JSON.stringify(result),/RAW_SECRET|body/);
  }
  const unknown=ui.errorInfo({detail:"RAW_SECRET",error:{category:"RAW_SECRET",diagnostic_id:"RAW_SECRET",http_status:"RAW_SECRET"}},500);
  assert.doesNotMatch(JSON.stringify(unknown),/RAW_SECRET/);assert.equal(unknown.kind,"error");
});
test("abstention is information, validation failure is an error",()=>{
  for(const reason of ["model_insufficient_evidence","model_abstained","empty_retrieval"])assert.equal(ui.answerInfo({abstained:true,metadata:{refusal_reason:reason}}).kind,"info");
  assert.equal(ui.answerInfo({abstained:true}).kind,"info");
  assert.equal(ui.answerInfo({abstained:true,metadata:{validation_status:"model_abstained"}}).kind,"info");
  assert.equal(ui.answerInfo({abstained:true,metadata:{refusal_reason:"citation_invalid"}}).kind,"error");
  assert.equal(ui.answerInfo({abstained:false}).label,"已验证");
});
test("incomplete-index explanations use service facts for all three causes",()=>{
  for(const message of ["尚未建立索引","索引需要更新","上次索引失败"]){const result=ui.answerInfo({metadata:{refusal_reason:"index_incomplete"}},{status:"failed",message});assert.equal(result.kind,"warning");assert.equal(result.message,message);}
  assert.match(ui.answerInfo({metadata:{refusal_reason:"index_incomplete"}},{status:"ready"}).message,/重新提问/);
});
test("local-only notices distinguish unconfigured, disabled and unverified",()=>{
  assert.match(ui.modelNotice({configured:false}),/尚未配置.*OCR/);
  assert.match(ui.modelNotice({configured:true,enabled:false}),/未启用/);
  assert.match(ui.modelNotice({configured:true,enabled:true,connection_verified:false}),/尚未验证/);
  assert.match(ui.modelNotice({configured:true,enabled:true,connection_verified:true}),/曾成功连接/);
});
const version={document_id:"doc",document_version_id:"v2",filename:"<script>long材料.pdf",page_count:3,version_number:2,content_sha256:"HASH",parser_version:"parser"};
test("materials show service state and place hashes in collapsed details",()=>{
  const html=ui.materialCard({version,status:"ready",label:"索引可用",message:"当前版本可用"});
  assert.match(html,/3 页 · 当前版本 v2/);assert.match(html,/索引可用/);assert.match(html,/<details class="technical">.*HASH/);assert.match(html,/document_version_id=v2/);assert.doesNotMatch(html,/<script>/);assert.match(html,/添加新版本/);
});
test("version links pin exact v1/v2 versions and never invent timestamps",()=>{
  const html=ui.versionHistory([version,{...version,document_version_id:"v1",version_number:1}]);
  assert.match(html,/v2（当前）/);assert.match(html,/document_version_id=v1/);assert.match(html,/document_version_id=v2/);assert.match(html,/未记录材料创建时间/);assert.doesNotMatch(html,/2026-/);
});
test("citations display true historical and current versions without changing original links",()=>{
  const item={document_id:"doc",document_version_id:"v1",filename:"证据.pdf",page_number:2,quote:"合成原文"};
  const labels=new Map([["v1","v1（非当前版本）"],["v2","v2（当前版本）"]]);
  const old=ui.evidence([item],labels);assert.match(old,/v1（非当前版本）/);assert.match(old,/document_version_id=v1#page=2/);
  assert.match(ui.evidence([{...item,document_version_id:"v2"}],labels),/v2（当前版本）/);
  assert.match(ui.evidence([item]),/版本信息暂不可用/);assert.doesNotMatch(ui.evidence([item]),/v0/);
});
test("citation version lookup uses existing document API once per source",async()=>{
  reset();const paths=[];global.fetch=async path=>{paths.push(path);return {ok:true,json:async()=>({latest:version,versions:[version,{...version,document_version_id:"v1",version_number:1}]})};};
  const labels=await ui.sourceVersions([{document_id:"doc"},{document_id:"doc"}]);assert.deepEqual(paths,["/api/documents/doc"]);assert.equal(labels.get("v1"),"v1（非当前版本）");assert.equal(labels.get("v2"),"v2（当前版本）");
});
test("system paths and restore instructions use actual safe config path and escape HTML",async()=>{
  reset();global.fetch=async()=>({ok:true,json:async()=>({...system,configuration_file:"C:/用户/config.env",data_directory:{writable:true,path:"C:/<data>"},database:{reachable:true,schema_status:"current",path:"C:/data.db"},logs:{file_logging_available:true,directory:"C:/logs"},api_key:"RAW_SECRET"})});
  await ui.refreshSystem();const html=nodes.get("system-content").innerHTML;assert.match(html,/技术详情/);assert.match(html,/C:\/用户\/config.env/);assert.match(html,/C:\/&lt;data&gt;/);assert.doesNotMatch(html,/RAW_SECRET|<data>/);
  const restored=ui.restoreResultHtml({data_directory:"C:/copy",database_path:"C:/copy/db",counts:{cases:1,documents:1,document_versions:2}});assert.match(restored,/C:\/用户\/config.env/);assert.doesNotMatch(restored,/本地 .env/);
});
test("late citation metadata cannot render a previous case",async()=>{
  reset();const wait=deferred();const answer={question:"旧案件",answer:"旧事实",claims:[{text:"旧事实",citation_ids:["E"]}],citations:[{evidence_id:"E",document_id:"doc",document_version_id:"v1"}]};
  global.fetch=async path=>path.startsWith("/api/documents/")?wait.promise:{ok:true,json:async()=>path.endsWith("/answers")?[answer]:{documents:[{}],status:"ready",message:"可用"}};
  const pending=ui.refresh("qa",ui.context());await new Promise(resolve=>setImmediate(resolve));ui.setState({current:"B",epoch:2});document.getElementById("qa-content").innerHTML="新案件";wait.resolve({ok:true,json:async()=>({latest:version,versions:[version]})});await pending;assert.equal(nodes.get("qa-content").innerHTML,"新案件");
});
test("temporary semantic failures survive GET merge, are labeled and suppress raw reason",()=>{
  const temporary={result_id:"temp",basis:"model_unavailable",verdict:"uncertain",contradiction_type:"action",reason:"RAW_SECRET",left_evidence:[],right_evidence:[]};
  const results=ui.mergeResults([{result_id:"saved",basis:"rule",verdict:"contradiction"}],[temporary]);
  assert.equal(results.length,2);assert.equal(results[1].temporary,true);
  const html=ui.contradictionCard(results[1],new Map());assert.match(html,/本次临时反馈/);assert.match(html,/未持久化/);assert.match(html,/待确认/);assert.match(html,/data-kind="warning"/);assert.doesNotMatch(html,/RAW_SECRET/);
  assert.equal(ui.mergeResults([temporary],[temporary]).length,1);
});
for(const key of ["upload","index","ask","extract","detect"]){
  test(`${key}: buttons really disable, duplicate calls are blocked, finally restores`,async()=>{
    reset();const wait=deferred();let calls=0;
    const operation=ui.run(key,"长操作",()=>{calls++;return wait.promise;});
    assert.ok(buttons.every(b=>b.disabled));assert.match(message.textContent,/合成测试案件.*长操作/);
    await ui.run(key,"长操作",()=>{calls++;});assert.equal(calls,1);
    wait.resolve({});await operation;assert.equal(buttons[0].disabled,false);assert.equal(buttons[2].disabled,false);assert.equal(buttons[3].disabled,true);assert.equal(message.textContent,"");
  });
}
test("failure restores buttons and does not display unsafe error detail",async()=>{
  reset();await ui.run("ask","提问",async()=>{throw ui.errorInfo({detail:"RAW_SECRET",error:{category:"timeout"}},503);});
  assert.equal(buttons[0].disabled,false);assert.equal(nodes.get("status").dataset.kind,"error");assert.doesNotMatch(nodes.get("status").textContent,/RAW_SECRET/);
});
test("A to B to A switch rejects late success and failure by generation",async()=>{
  for(const fails of [false,true]){
    reset();const wait=deferred();let afterCalls=0;
    const operation=ui.run("ask","提问",()=>wait.promise,()=>{afterCalls++;});
    ui.setState({current:"B",epoch:2});ui.setState({current:"A",epoch:3});nodes.get("status").textContent="当前新状态";
    if(fails)wait.reject(ui.errorInfo({},500));else wait.resolve({});await operation;
    assert.equal(afterCalls,0);assert.equal(nodes.get("status").textContent,"当前新状态");assert.equal(ui.pending.size,0);
  }
});
test("late refresh cannot overwrite another case or later refresh",async()=>{
  reset();const old=deferred(),newer=deferred();let calls=0;
  global.fetch=()=>{const value=(calls++===0?old:newer);return value.promise;};
  const one=ui.refresh("timeline",ui.context()),two=ui.refresh("timeline",ui.context());
  const response=fact=>({ok:true,json:async()=>[{date_status:"unknown",fact,contradiction_result_ids:[],evidence:[]}]});
  newer.resolve(response("最新内容"));await two;old.resolve(response("旧内容"));await one;
  assert.match(nodes.get("timeline-content").innerHTML,/最新内容/);assert.doesNotMatch(nodes.get("timeline-content").innerHTML,/旧内容/);
  const wait=deferred();global.fetch=()=>wait.promise;const request=ui.refresh("timeline",ui.context());ui.setState({current:"B",epoch:2});nodes.get("timeline-content").innerHTML="B 的页面";wait.resolve(response("A 的旧内容"));await request;assert.equal(nodes.get("timeline-content").innerHTML,"B 的页面");
});
test("case creation double-submit guard and switch race",async()=>{
  reset();const wait=deferred();let calls=0;const controls=[element("BUTTON"),element("INPUT")];const localMessage=element();
  nodes.set("case-form",{querySelectorAll:()=>controls,querySelector:()=>localMessage,reset(){throw Error("old creation must not reset new case");}});nodes.set("case-name",{value:"合成新案件"});
  global.fetch=()=>{calls++;return wait.promise;};const event={preventDefault(){}};
  const create=ui.submitCase(event);assert.ok(controls.every(b=>b.disabled));await ui.submitCase(event);assert.equal(calls,1);
  ui.setState({current:"B",epoch:2});wait.resolve({ok:true,json:async()=>({case_id:"created"})});await create;assert.equal(ui.context().id,"B");assert.ok(controls.every(b=>!b.disabled));
});
test("case switch during creation list refresh does not reset form or replace status",async()=>{
  reset();const listing=deferred();let calls=0;const controls=[element("BUTTON"),element("INPUT")];const localMessage=element();let resets=0;
  nodes.set("case-form",{querySelectorAll:()=>controls,querySelector:()=>localMessage,reset(){resets++;}});nodes.set("case-name",{value:"合成新案件"});
  global.fetch=async()=>{calls++;return calls===1?{ok:true,json:async()=>({case_id:"created"})}:listing.promise;};
  const create=ui.submitCase({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,2);
  ui.setState({current:"B",epoch:2});document.getElementById("status").textContent="B 的新状态";listing.resolve({ok:true,json:async()=>({cases:[{case_id:"created",name:"合成新案件"}]})});await create;
  assert.equal(ui.context().id,"B");assert.equal(resets,0);assert.equal(nodes.get("status").textContent,"B 的新状态");
});
test("rename keeps ID and synchronizes select, heading and document title",async()=>{
  reset();const select=document.getElementById("case-select"),option=element("OPTION");option.value="A";select.append(option);
  global.fetch=async(path,options)=>({ok:true,json:async()=>path.endsWith("/name")?{case_id:"A",name:"新的合成名称",status:"active"}:system});
  await ui.renameCurrentCase("新的合成名称");assert.equal(ui.context().id,"A");assert.equal(option.textContent,"新的合成名称");assert.match(nodes.get("case-heading").textContent,/新的合成名称/);assert.match(document.title,/新的合成名称/);
});
test("rename late response cannot overwrite another case heading",async()=>{
  reset();const wait=deferred();global.fetch=()=>wait.promise;const rename=ui.renameCurrentCase("旧案件新名");ui.setState({current:"B",epoch:2});document.getElementById("case-heading").textContent="B 的标题";wait.resolve({ok:true,json:async()=>({case_id:"A",name:"旧案件新名",status:"active"})});await rename;assert.equal(nodes.get("case-heading").textContent,"B 的标题");
});
test("archive filter sends explicit query and archived cases show writable status",async()=>{
  reset();document.getElementById("include-archived").checked=true;const paths=[];
  global.fetch=async(path)=>{paths.push(path);return {ok:true,json:async()=>path.startsWith("/api/cases?")?{cases:[{case_id:"A",name:"归档合成案件",status:"archived"}]}:{status:"not_indexed",documents:[],action_label:"建立索引",label:"未建立",message:"请导入"}};};
  await ui.loadCases("A",1);assert.match(paths[0],/include_archived=true/);assert.match(nodes.get("case-heading").textContent,/已归档，仍可分析/);assert.equal(nodes.get("archive-case").textContent,"恢复归档");
});
test("downloads ignore arbitrary returned URLs and use generated IDs only",()=>{
  const id="00000000-0000-0000-0000-000000000001";
  const html=ui.downloadLink({artifact_id:id,filename:"report.json",download_url:"https://evil.invalid/.env"},"下载");assert.match(html,new RegExp("/api/data/artifacts/"+id));assert.doesNotMatch(html,/evil/);assert.doesNotMatch(ui.downloadLink({artifact_id:"../.env"},"下载"),/href=/);
});
test("restore preview includes explicit copy-only acknowledgement and counts",()=>{
  const html=ui.restorePreviewHtml({restore_id:"id",message:"已验证",counts:{cases:2,documents:3,document_versions:4}});assert.match(html,/type="checkbox"/);assert.match(html,/不覆盖、不删除、不自动切换/);assert.match(html,/历史版本 4/);assert.match(html,/确认创建新副本/);
});
test("restore confirmation refuses an unchecked acknowledgement",async()=>{
  reset();document.getElementById("restore-understood").checked=false;let calls=0;global.fetch=async()=>{calls++;};await ui.confirmRestoreButton({dataset:{restoreId:"id"}});assert.equal(calls,0);assert.match(nodes.get("data-operation").textContent,/请先确认理解/);
});
test("system data operations disable, guard duplicates and recover after success/failure",async()=>{
  for(const fails of [false,true]){
    reset();const wait=deferred();let calls=0;const job=ui.runGlobal("备份",()=>{calls++;return wait.promise;},()=>{});assert.ok(buttons.every(b=>b.disabled));await ui.runGlobal("备份",()=>{calls++;},()=>{});assert.equal(calls,1);
    if(fails)wait.reject(ui.errorInfo({error:{category:"missing_pdf"}},422));else wait.resolve({});await job;assert.ok(buttons.every(b=>!b.disabled));assert.match(nodes.get("data-operation").textContent,fails?/未完成/:/完成/);
  }
});
test("restore success provides both paths and a manual restart sequence",()=>{
  const html=ui.restoreResultHtml({data_directory:"C:\\safe\\restored",database_path:"C:\\safe\\restored\\case.db",counts:{cases:1,documents:2,document_versions:3}});assert.match(html,/CASEFILE_DATA_DIR=C:/);assert.match(html,/CASEFILE_DATABASE_URL=sqlite:\/\/\/C:\/safe/);assert.match(html,/停止当前服务/);assert.match(html,/保留原路径/);
});
