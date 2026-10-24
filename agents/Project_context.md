# Building your Agentic AI CAD System: Implementation Roadmap

Your project is essentially an AI-powered engineering team that takes a user's idea, turns it into a functional CAD model in Onshape, evaluates its manufacturability, and iteratively improves it.

The key to making this work during a hackathon is to separate three things:

1. AI reasoning: Gemma interprets requirements, evaluates designs, and proposes engineering decisions.
2. Agent orchestration: Your backend decides which agent works next, what information it receives, and when user approval is required.
3. CAD execution: Deterministic Python tools translate approved geometry instructions into actual Onshape operations.

I recommend building the Designer and Machinist first, as you've planned, but establishing the data structures and communication protocols for the Engineer at the beginning.

## 1. Overall system architecture

User

Describes part or assembly requirements

Designer Agent

Gemma

Requirements gathering → Concept design → Sketch → User approval

Approved design + feedback

Engineer Agent

Gemma

Design review → Modeling plan → Onshape CAD creation → Validation → Technical memo

Engineer A

Engineer B

Engineer C

Optional parallel workers for complex assemblies

Approved CAD + manufacturing feedback

Machinist Agent

Gemma

Process selection → Material costs → Machine time → Labor → Optimization recommendations

Loop back to Designer / Engineer as needed

Final deliverables

Onshape assembly · Engineering memo · Manufacturing cost analysis

The important architectural decision is that you do not need three separately trained models. You can run multiple specialized agents using one locally hosted Gemma model.

Each agent is differentiated by its system instructions, permitted tools, expected outputs, and access to project information. This is substantially easier to implement and maintain than fine-tuning separate models.

## 2. Recommended technology stack

| Component           | Technology                                 | Responsibility                        |
| ------------------- | ------------------------------------------ | ------------------------------------- |
| Local LLM           | Gemma 3 via Ollama                         | Reasoning for all agents              |
| Backend             | Python + FastAPI                           | Agent execution and API requests      |
| Agent orchestration | Python state machine, optionally LangGraph | Workflow, routing, approvals          |
| Structured outputs  | Pydantic                                   | Validating agent responses            |
| CAD system          | Onshape REST API                           | Creating and modifying models         |
| Frontend            | Streamlit initially; React if time permits | Chat, previews, approvals             |
| Persistent storage  | SQLite or JSON for MVP                     | Design state, revisions, decisions    |
| Cost calculations   | Python functions + material/rate tables    | Deterministic manufacturing estimates |

LangGraph is particularly relevant because it supports human approval checkpoints, persistent state, and branching execution. However, a manually controlled Python state machine will be quicker if your immediate responsibility is just establishing the Designer and Machinist.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.langchain.com\&sz=32)

LangChain

+1



## 3. Step one: Set up Gemma locally

For your laptop with a mobile RTX 5070 and 32 GB RAM, I would start with Gemma 3 4B, then benchmark Gemma 3 12B.

Gemma 3 4B

# 3.4 GB

Approximate Ollama model download size

Start here

Better for responsive conversations and testing multiple agents.

Gemma 3 12B

# 8.2 GB

Approximate Ollama model download size

Worth benchmarking for harder engineering decisions, but may spill into system RAM depending on available VRAM.

These are model sizes, not total inference memory requirements. Both are listed in Ollama's current Gemma library.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://ollama.com\&sz=32)

Ollama

+1



### Installation

1\. Install Ollama

Get it from [ollama.com/download](https://ollama.com/download?utm_source=chatgpt.com) and open PowerShell.

2\. Download Gemma

```
ollama pull gemma3:4b
ollama run gemma3:4b
```

Test it with a simple design question, then exit the interactive session.

3\. Create your Python environment

```
mkdir cad-agents
cd cad-agents

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install ollama pydantic fastapi uvicorn requests python-dotenv streamlit
```

4\. Verify Python can reach Gemma

```
from ollama import chatresponse = chat(    model="gemma3:4b",    messages=[        {            "role": "user",            "content": "What information do you need to design a mounting bracket?"        }    ],)print(response.message.content)
```

If it works, you've established the local inference backend. Ollama normally exposes its API at `http://localhost:11434`.

### How you should handle tool calling

For your first implementation, I recommend structured JSON outputs rather than giving the model unrestricted access to tools.

Ollama supports JSON-schema-constrained outputs. This lets Gemma decide what should happen while Python validates the decision before execution.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub

+1



This is useful because the standard Gemma 3 model is not the same as a Gemma variant specifically adapted for native tool calling. Such variants exist, but they add another behavior you would need to test.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://ollama.com\&sz=32)

Ollama

+1



Your initial flow should be:

Gemma generates a decision

Structured JSON

Pydantic + Python validate it

Dimensions, allowed actions, required data

Backend executes approved actions

Sketching, calculations, Onshape API calls

## 4. Step two: Implement the Designer and Machinist as separate agents

An agent should have four components: its role, its inputs, its allowed outputs or tools, and its success criteria.

### Designer Agent

Its purpose is converting a vague idea into an actionable design specification.

Inputs: User descriptions, previous answers, constraints, preferences, and existing design revisions.

Responsibilities:

- Ask about purpose, functional requirements, dimensions, quantity, loads, and environmental conditions when relevant.
- Identify critical missing information rather than inventing specifications.
- Suggest suitable initial geometries and materials.
- Prepare a structured concept that can be rendered as a 2D sketch.
- Incorporate feedback until the user approves.

Outputs: A design specification, sketch parameters, questions, and eventually an approval-ready concept.

For example, a user says, "I need a bracket to hold a small motor." The Designer should determine mounting configuration, available space, hole locations, motor mass and operating conditions before proposing a geometry.

### Machinist Agent

Its purpose is design for manufacturability and assembly (DFMA) and cost optimization.

Inputs: Approved CAD geometry, material, manufacturing quantity, tolerance requirements, production processes, and available cost data.

Responsibilities:

- Compare appropriate manufacturing methods, such as 3D printing, CNC machining, and laser cutting.
- Identify features that increase machining or fabrication costs.
- Estimate material usage, setup time, machine time, and labor.
- Recommend design changes that reduce cost without violating functional constraints.
- Return an itemized cost estimate and an explanation of its assumptions.

Outputs: Manufacturing assessment, costed process options, recommended process, and any suggested design revision.

### A minimal working Python implementation

Create a file called `agents.py`. This establishes two agents calling the same local Gemma model, with different prompts and validated outputs.

```
import jsonfrom typing import Literalfrom pydantic import BaseModel, Fieldfrom ollama import chatMODEL = "gemma3:4b"# Designer's response contractclass DesignerResult(BaseModel):    status: Literal["needs_info", "concept_ready"]    questions: list[str] = Field(default_factory=list)    part_name: str | None = None    purpose: str | None = None    dimensions_mm: dict[str, float] = Field(default_factory=dict)    proposed_material: str | None = None    geometry_description: str | None = None    assumptions: list[str] = Field(default_factory=list)# Machinist's response contractclass MachinistResult(BaseModel):    recommended_process: str    reasoning: str    design_improvements: list[str]    cost_drivers: list[str]    missing_cost_data: list[str]DESIGNER_PROMPT = """You are Designer, an AI product design agent.Your responsibility is to turn a user's ideainto a preliminary engineering design concept.
```

This is a working starting point for inference and output validation, not yet the complete agent workflow. It doesn't currently create sketches, persist user replies, execute CAD operations, or compute prices.

Once the Designer has enough information, its structured specification becomes the input for the Engineer. The Machinist receives the Engineer's completed design and measured geometry.

The key benefit is that every agent uses an explicit communication contract. If you later replace Gemma 4B with a different model, the other parts of your system do not need to change.

## 5. Step three: Define how agents communicate

This is one of the most important parts of your project.

Do not have agents communicate exclusively through conversational text. Instead, maintain one structured project state that all agents can read and update through the orchestrator.

For example, this could be the data object passed between your Designer, Engineer, and Machinist:

```

{
  "project_id": "motor-bracket-001",
  "revision": 1,
  "stage": "designer_approval",
  "requirements": {
    "purpose": "Mount a motor to a frame",
    "max_width_mm": 100,
    "max_height_mm": 80,
    "material": "Aluminum 6061",
    "quantity": 10,
    "load_requirements": null
  },
  "design": {
    "name": "Motor Mounting Bracket",
    "geometry": [],
    "sketch_url": null,
    "approved": false
  },
  "engineering": {
    "feature_plan": [],
    "onshape_document_id": null,
    "validation": null,
    "memo": null,
    "approved": false
  },
  "manufacturing": {
    "candidate_processes": [],
    "recommended_process": null,
    "estimated_unit_cost": null,
    "recommendations": []
  },
  "history": []
}

```

These values are illustrative; unknown specifications such as load requirements stay `null` until established.

Your orchestrator manages transitions between stages. Agents should not independently decide to bypass approval.

### Routing logic

| Current stage | Condition                       | Next stage                             |
| ------------- | ------------------------------- | -------------------------------------- |
| Designer      | Missing critical information    | Ask user                               |
| Designer      | Sketch generated                | Await approval                         |
| Designer      | Concept approved                | Engineer                               |
| Engineer      | Problems identified             | User review, then Designer or Engineer |
| Engineer      | CAD generated                   | Validate model                         |
| Engineer      | Memo approved                   | Machinist                              |
| Machinist     | Design revision needed          | Engineer or Designer                   |
| Machinist     | Cost/manufacturability accepted | Final deliverable                      |

For example, a request to change the overall shape should return to the Designer. A request to increase a fillet radius for machining should generally return to the Engineer, because the design intent has not changed.

Important: Use revision numbers and a maximum iteration count. Otherwise, the agents may continuously disagree with one another.

## 6. Step four: Connect the system to Onshape

This is the biggest technical integration challenge.

Fortunately, Onshape exposes REST APIs for creating Part Studios, sketches, features, and assemblies. Its documented workflow includes sketch creation, extrusion, feature updates, and retrieving geometry properties.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://onshape-public.github.io\&sz=32)

onshape-public.github.io

+2



### A. Establish API access

1. Open your Onshape account settings and navigate to Developer.
2. Generate an API access key and secret with the permissions needed for your test documents.
3. Put them in a local `.env` file.
4. Write a Python function that authenticates and reads a test document.
5. Create a new Part Studio through the API.

For a local proof of concept, Onshape documents Basic authentication with API keys, although signed requests offer better security. OAuth2 is required for a distributable Onshape app.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://onshape-public.github.io\&sz=32)

onshape-public.github.io



### B. Build a restricted CAD tool library

Instead of letting the Engineer construct arbitrary Onshape requests, give it a collection of known operations:

```
# Interface contracts; implement using Onshape's API.create_document(name)create_part_studio(document_id, workspace_id, name)create_sketch(part_studio_id, geometry)extrude(sketch_id, depth_mm)add_hole(part_id, position, diameter_mm)add_fillet(part_id, edge_id, radius_mm)get_features(part_studio_id)get_mass_properties(part_studio_id)create_assembly(document_id)insert_part(assembly_id, part_id)
```

These are proposed wrapper functions, not built-in Onshape Python functions.

Start with only `create_sketch` and `extrude`. Add other features after those work reliably.

The Onshape feature endpoint documented for this workflow is:

`POST /api/v9/partstudios/d/{did}/w/{wid}/e/{eid}/features`

A sketch uses the `BTMSketch-151` feature type, and an extrusion uses the `extrude` feature type. The documentation has example request bodies for both.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://onshape-public.github.io\&sz=32)

onshape-public.github.io



One especially effective technique is to manually create a simple feature in Onshape, retrieve its JSON using `getPartStudioFeatures`, and build reusable templates from that output. Onshape explicitly recommends inspecting existing feature definitions because feature parameter formats can change.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://onshape-public.github.io\&sz=32)

onshape-public.github.io



### C. Create an intermediate geometry language

This prevents the Engineer from needing to understand every implementation detail of the Onshape REST API.

For example, the Engineer could output:

```
{
  "part_name": "Mounting Plate",
  "units": "mm",
  "operations": [
    {
      "type": "rectangle_sketch",
      "width": 80,
      "height": 60,
      "plane": "Top"
    },
    {
      "type": "extrude",
      "depth": 5
    },
    {
      "type": "hole_pattern",
      "diameter": 6,
      "positions": [
        [-30, -20],
        [30, -20],
        [-30, 20],
        [30, 20]
      ]
    }
  ]
}
```

Your Python geometry compiler would translate each operation into validated Onshape API requests.

Add validation for positive dimensions, hole locations, unit conversions, permitted operations, and references to existing sketches or faces.

For the Designer's preview, you can render the same geometry description into SVG with dimension labels. This is considerably more reliable for an MVP than asking a text model to generate an accurate engineering drawing as an image.

After user approval, the Engineer can build the actual parametric CAD model.

## 7. Step five: Implement real manufacturing cost analysis

This is where your Machinist can contribute something more substantial than generic AI recommendations.

The model should interpret the geometry and recommend manufacturing methods. Python should calculate the numbers.

### A. Collect manufacturing data

The Machinist needs:

| Input                   | Where to obtain it                   |
| ----------------------- | ------------------------------------ |
| Part volume             | Onshape mass properties              |
| Raw material stock size | Stock dimensions or process estimate |
| Material density        | Materials table                      |
| Material price          | Configured price database            |
| Machine hourly rate     | Configured process-rate table        |
| Machining time          | Process-specific estimate            |
| Operator time           | Process-specific estimate            |
| Batch quantity          | Designer requirements                |
| Setup costs             | Configured process assumptions       |

Onshape's Part Studio mass-properties API can report volume and mass, provided the relevant material properties are available.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://onshape-public.github.io\&sz=32)

onshape-public.github.io



### B. Implement a deterministic cost model

A simple first approximation is:

\\[ C\_{\text{unit}} = C\_{\text{material}}+ C\_{\text{machine}}+ C\_{\text{labor}}+ \frac{C\_{\text{setup}}}{Q} \\]

Where \\(Q\\) is manufacturing quantity.

For example:

```
def calculate_unit_cost(    material_cost,    machine_minutes,    machine_rate_per_hour,    labor_minutes,    labor_rate_per_hour,    setup_cost,    quantity):    if quantity <= 0:        raise ValueError("Quantity must be positive")    machine_cost = (        machine_minutes / 60 * machine_rate_per_hour    )    labor_cost = (        labor_minutes / 60 * labor_rate_per_hour    )    return (        material_cost        + machine_cost        + labor_cost        + setup_cost / quantity    )
```

For a real estimate, material costs should account for purchased stock and waste, not just finished volume. You should also distinguish labor that is already included in a quoted machine rate from separately charged labor.

### C. Compare alternative processes

For an initial demonstration, let the Machinist consider three processes.

| Process         | Potential advantage                 | Common limitation                                       |
| --------------- | ----------------------------------- | ------------------------------------------------------- |
| FDM 3D printing | Low setup cost, complex geometry    | Layer-dependent strength and slower per-unit production |
| CNC milling     | Accuracy and broad material support | Tool access, fixtures, setup time                       |
| Laser cutting   | Efficient for flat sheet parts      | Limited to 2D profiles and sheet-based geometry         |

The Machinist can identify likely cost drivers such as unnecessary thickness, difficult-to-machine pockets, tight tolerances, or excess support material.

It should then ask the Python calculator to evaluate the alternatives using explicit assumptions. Missing times or rates should be marked as unavailable, rather than fabricated.

At this stage, your numbers are preliminary cost estimates, not validated supplier quotations.

## 8. Step six: Add the Engineer Agent and parallel execution

Once your two agents and Onshape interface are functioning, the Engineer can be integrated using the same structure.

I would divide its work into four phases.

1. Feasibility review: Identify issues such as insufficient clearances, impossible geometry, weak mounting arrangements, or missing critical information. Use rule-based checks wherever possible rather than relying entirely on LLM judgment.
2. Modeling plan: Decompose the approved concept into individual parts and ordered CAD operations.
3. CAD execution and validation: Apply the feature plan, inspect the resulting feature statuses and geometry, recover from failed operations, and prepare a technical memo.
4. Manufacturing handoff: Submit the approved revision, material selection, measured geometry, and engineering assumptions to the Machinist.

### Parallel Engineer Agents

If an assembly has several independent parts, the Engineer could create separate tasks.

For instance:

Lead Engineer

Decomposes assembly and defines shared dimensions/interfaces

Housing

Worker

Shaft

Worker

Mount

Worker

Assembly Integration

Insert parts, establish mates, validate interfaces

Onshape supports inserting parts into assemblies, creating mate connectors, and defining mates. Separate Part Studios and workspaces can help isolate parallel operations.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://onshape-public.github.io\&sz=32)

onshape-public.github.io

+1



However, I would not initially implement dynamically spawned engineers. Parallel CAD editing introduces shared-reference and synchronization problems. Start with one Engineer and add a fixed two-worker demonstration only if the core workflow is reliable.

## 9. Recommended project structure

```
cad-agents/
│
├── agents/
│   ├── designer.py
│   ├── engineer.py
│   └── machinist.py
│
├── prompts/
│   ├── designer.txt
│   ├── engineer.txt
│   └── machinist.txt
│
├── schemas/
│   ├── design.py
│   ├── geometry.py
│   └── manufacturing.py
│
├── services/
│   ├── gemma.py
│   ├── onshape.py
│   ├── sketch_renderer.py
│   ├── cost_calculator.py
│   └── cad_validator.py
│
├── orchestration/
│   ├── workflow.py
│   └── project_state.py
│
├── data/
│   └── process_rates.json
│
├── app.py
├── requirements.txt
└── .env
```

The most useful organizational choice is keeping agent reasoning separate from executable tools. You can work on the Designer and Machinist while another teammate develops the Onshape integration, provided everyone agrees on the same data schemas.

## 10. Hackathon implementation order

For a roughly 48-hour hackathon, I would use the following priorities.

Hours 0–4

Local AI and schemas

First

Set up Ollama, verify Gemma JSON outputs, define shared project state.

Hours 4–10

Designer Agent

Build multi-turn requirements gathering, concept specification, and SVG preview.

Hours 10–16

Machinist Agent

Add manufacturing process selection, reference tables, cost calculations, and feedback.

Hours 16–28

Onshape integration

Create a document, sketch, and extruded part. Verify geometry was created correctly.

Hours 28–36

Engineer and workflow integration

Connect agent handoffs, approvals, and simple engineering review.

Hours 36–42

End-to-end testing

Run complete designs and test rejection, revision, and API failure cases.

Hours 42–48

Demo and presentation

Polish UI, technical memo, cost breakdown, and demonstrate the working workflow.

These are suggested allocations, not estimates of guaranteed implementation time.

## 11. The MVP I would aim to demonstrate

I would choose a custom mounting bracket rather than a complicated assembly for your first end-to-end demonstration.

[How to Read an Engineering Drawing – a Simple Guide | Make UK](https://images.openai.com/static-rsc-4/0zbDwYxQ_Mmg5meDXhTziKD7Bwz675Bh5WgsG9NW01L6E7k_9KJnVYwCsoIuRauxqBeoCHgbQJfUKNTfkhcuEhfrUWDP8blWAl9H0jkUNjEM-5v-tMOddD-AHHqu7JrZiQRzE0kId6lmWuRqAl0blsC-rv73NT6CLXHxsEG8oE4?purpose=inline)

[Support standard équerre pour feux L52 - Standby France](https://images.openai.com/static-rsc-4/q3cvV5osCER5ZZOT7I3bvr97t023HWs_t23GglWgFq9X2DOfs4VEzhKjTQvwXojRP50JyThasYUNXiX_LsUs1SGSbBWMQNqOzN1LMFXmrGtsWuFsCsUe2Lyel9h-X3epWynMKDc80nmbm8119IgqmxAW8ixOQsbl_4Q_5yQOQ2U?purpose=inline)

[What’s New: Configuration Input Visibility, Document/Folder Properties - 1.192](https://images.openai.com/static-rsc-4/HuHu4PDec5YPtTEW7E-cWZNvqz1gtnrhvahxMjtkWJfdj6b57IVkOZQeftQVo7PhciTmwx_ITMK4ZAqrLtjoNjC6sChdUhtbEh8oQ_JWitTar6YA-tlz8kyJBfckeCAWR5EvNUSgnh6-SO0Yd35V-GxlM3kYlkAxXNBZ7ILXFZU?purpose=inline)

8

A complete demonstration could proceed as follows:

1. User: "Design a bracket for mounting a small motor."
2. Designer: Asks about motor dimensions, mounting holes, available space, loads, and preferred material.
3. Designer: Generates a dimensioned concept sketch and requests approval.
4. Engineer: Identifies clearance or mounting concerns, proposes any necessary revision, and receives approval.
5. Engineer: Creates the sketch and 3D features in Onshape.
6. Engineer: Produces a technical memo with dimensions, materials, engineering assumptions, and unresolved validation items.
7. Machinist: Compares relevant manufacturing methods, calculates estimated costs, and recommends cost-saving geometry changes.
8. User: Approves a revision, and the final Onshape design updates accordingly.

Your demo has a particularly strong story if the Machinist can demonstrate an actual cost-related design change rather than merely generate a report.

For instance, it could recommend changing a machined part to a flat sheet-metal design where the loads and assembly constraints permit. The Engineer then updates the CAD model, and the Machinist recalculates the cost difference.

## 12. What I would prioritize for your specific assignment

Since your immediate responsibility is setting up Gemma and the Designer and Machinist, I would define these completion milestones:

Implementation checklist

0 of 9 completed

Gemma 3 4B runs locally and responds through Python

Designer asks relevant questions without inventing critical requirements

Designer outputs a validated geometry/design JSON object

Designer produces a reproducible dimensioned SVG concept

Design approval and rejection update persistent project state

Machinist accepts engineering/design data through a shared schema

Machinist compares processes and calculates costs through Python

Machinist outputs actionable design improvements

Both agents integrate with a mock Engineer handoff

The reason to use a mock Engineer handoff initially is that you can finish and test the Designer/Machinist interface independently of the CAD integration.

My highest-priority technical recommendations are:

- Use one shared Gemma model with specialized prompts, not two independent model installations.
- Use Pydantic schemas and a shared project state, not free-form conversation as the agent communication protocol.
- Use Python for CAD commands and manufacturing calculations; use Gemma for planning, interpretation, and recommendations.
- Get one reliable Onshape sketch-to-extrusion workflow working before attempting multi-part assemblies.
- Preserve human approval and mechanical validation checkpoints. LLM-generated engineering claims are not substitutes for tested strength, safety, tolerance, or manufacturing feasibility.

The biggest achievement for this hackathon would be demonstrating that your agents can take a requirement, make an actual parametric CAD change, evaluate its manufacturing implications, and revise it coherently.

That closed feedback loop is what will make your project more compelling than a conventional text-to-CAD generator.