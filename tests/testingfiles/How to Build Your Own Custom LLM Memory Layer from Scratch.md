---
title: "How to Build Your Own Custom LLM Memory Layer from Scratch"
source: "https://towardsdatascience.com/how-to-build-your-own-custom-llm-memory-layer-from-scratch/"
author:
  - "[[Avishek Biswas]]"
published: 2026-02-04
created: 2026-04-09
description: "Step-by-step guide to building autonomous memory retrieval systems"
tags:
  - "clippings"
---
is a fresh start. Unless you explicitly supply information from previous sessions, the model has no built‑in sense of continuity across requests or sessions. This stateless design is great for parallelism and safety, but it poses a huge challenge for chat applications that requires user-level personalization.

> If your chatbot treats the user as a stranger every time they log in, how can it ever generate personalized responses?

In this article, we will build a simple memory system from scratch, inspired by the popular Mem0 architecture.

Unless otherwise mentioned, all illustrations embedded here were created by me, the author.

The goal of this article is to educate readers on memory management as a [**context engineering problem**](https://towardsdatascience.com/context-engineering-a-comprehensive-hands-on-tutorial-with-dspy/). At the end of the article you will also find:

- **A GitHub link** that contains the full memory project, you can host yourself
- An in-depth **YouTube tutorial** that goes over the concepts line by line.

## Memory as a Context Engineering problem

Context Engineering is the technique of filling in the context of an LLM with all the relevant information it needs to complete a task. In my opinion, memory is one of the hardest and most interesting context engineering problems.

![](https://contributor.insightmediagroup.io/wp-content/uploads/2026/01/Slide5-1024x576.jpeg)

LLMs do not come with memory!

Tackling memory introduces you (as a developer) to some of the most important techniques required in almost all context engineering problems, namely:

1. Extracting structured information from raw text streams
2. Summarization
3. Vector databases
4. Query generation and similarity search
5. Query post-processing and re-ranking
6. Agentic tool calling

And so much more.

> ***As we are building our memory layer from scratch, we will have to apply all of these techniques! Read on.***

## High‑level architecture

At a glance, the system should be able to do four things: **extract**, **embed, retrieve, and maintain**. Let’s scout the high-level plans before we begin the implementation.

### Components

• **Extraction**: Extracts candidate atomic memories from the current user-assistant messages.  
• **Vector DB**: Embed the extracted factoids into continuous vectors and store them in a vector database.  
• **Retrieval**: When the user asks a question, we will generate a query with an LLM and retrieve memories similar to that query.

• **Maintenance**: Using a ReAct (Reasoning and Acting) loop, the agent decides whether to add, update, delete, or no‑op based on the turn and contradictions with existing facts.

![](https://contributor.insightmediagroup.io/wp-content/uploads/2026/01/CleanShot-2026-01-12-at-02.11.15@2x-1024x523.png)

The Mem0 architecture (Source: Mem0 paper )

> ***Importantly, every step above should be optional.** If the LLM agent does not need access to previous memories to answer a question, it should not try to search our vector database at all.*
> 
> *The strategy is to provide the LLM all the tools it can need to accomplish the tasks, along with clear instructions of what each tool does – and rely on the LLM’s intelligence to use these tools autonomously*!

Let’s see this in action!

## 2) Memory Extraction with DSPy: From Transcript to Factoids

In this section, let’s design a robust extraction step that converts conversation transcripts into a handful of atomic, categorized factoids.

![](https://contributor.insightmediagroup.io/wp-content/uploads/2026/01/image-9-1024x576.jpeg)

The image shows a diagram of extracting relevant facts from user’s messages and storing them in memory.

### What we are extracting and why it matters

**The goal is to make a memory store that is a per-user, persistent vector-backed database.**

> **What is a “good” memory?**
> 
> A short, self-contained fact—an atomic unit—that can be embedded and retrieved later with high precision.

With DSPy, extracting structured information is very straightforward. Consider the code snippet below.

- We define a DSPy signature called `MemoryExtract`.
- The inputs of this signature (annotated as `InputField`) are the transcript,
- and the expected output (annotated as `OutputField`) is a list of strings containing each factoid.

**Context string in, list of memory strings out.**

```python
# ... other imports
import dspy
from pydantic import BaseModel

class MemoryExtract(dspy.Signature):
    """
Extract relevant information from the conversation. 
Memories are atomic independent factoids that we must learn about the user.
If transcript does not contain any information worth extracting, return empty list.
"""

    transcript: str = dspy.InputField()
    memories: list[str] = dspy.OutputField()

memory_extractor = dspy.Predict(MemoryExtract)
```

In DSPy, the signature’s docstring is used as a system prompt. We can customize the docstring to explicitly tailor the kind of information that the LLM will extract from the conversation.

Finally, to extract memories, we pass the conversation history into the memory extractor as a JSON string. Check out the code snippet below.

```python
async def extract_memories_from_messages(messages):
    transcript = json.dumps(messages)
    with dspy.context(lm=dspy.LM(model=MODEL_NAME)):
        out = await memory_extractor.acall(transcript=transcript)
    return out.memories # returns a list of memories
```

That’s it! Let’s run the code with a dummy conversation and see what happens.

```python
if __name__ == "__main__":
    messages = [
        {
            "role": "user",
            "content": "I like coffee"
        },
        {
            "role": "assistant",
            "content": "Got it!"
        },
        {
            "role": "user",
            "content": "actually, no I like tea more. I also like football"
        }
    ]
    memories = asyncio.run(extract_memories_from_messages(messages))
    print(memories)

'''
Outputs:

[
    "User used to like tea, but does not anymore",
    "User likes coffee",
    "User likes football"
]
'''
```

As you can see, we can extract independent factoids from conversations. What does this mean?

> ***We can save the extracted factoids in a database that exists outside the chat session.***

If DSPy interests you, check out this [Context Engineering with DSPy](https://towardsdatascience.com/context-engineering-a-comprehensive-hands-on-tutorial-with-dspy/) article that goes deeper into the concept. Or watch this video below

Please accept cookies to access this content

## Embedding extracted memories

So we can extract memories from conversations. Next, let’s embed them so we can eventually store them in a vector database.

In this project, we will use **QDrant** as our vector database – they have a cool free tier that is extremely fast and supports additional features like hybrid filtering (where you can pass SQL “where”-like attribute filters to your vector query search).

![](https://contributor.insightmediagroup.io/wp-content/uploads/2026/01/image-3-1024x576.jpeg)

The image shows the process of uploading memories into a vector database.

### Choosing the embedding model and fixing the dimension

For cost, speed, and solid quality on short factoids, we choose **text-embedding-3-small**. We pin the vector size to 64, which lowers storage and speeds up search while remaining expressive enough for concise memories. This is a hyperparam we can tune later to suit our needs.

```python
client = openai.AsyncClient()
async def generate_embeddings(strings: list[str]):
    out = await client.embeddings.create(
        input=strings,
        model="text-embedding-3-small",
        dimensions=64
    )
    embeddings = [item.embedding for item in out.data]
    return embeddings
```

To insert into QDrant, let’s create our databases first and create an index on `user_id`. This will let us quickly filter our records by users.

```python
from qdrant_client import AsyncQdrantClient
COLLECTION_NAME = "memories"
async def create_memory_collection():
    if not (await client.collection_exists(COLLECTION_NAME)):
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=64, distance=Distance.DOT),
        )

        await client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="user_id",
            field_schema=models.PayloadSchemaType.INTEGER
        )
```

I like to define contracts using Pydantic at the top so that other modules know the output shape of these functions.

```python
from pydantic import BaseModel

class EmbeddedMemory(BaseModel):
    user_id: int
    memory_text: str
    date: str
    embedding: list[float]

class RetrievedMemory(BaseModel):
    point_id: str
    user_id: int
    memory_text: str
    date: str
    score: float
```

Next, let’s write helper functions to insert, delete, and update memories.

```python
async def insert_memories(memories: list[EmbeddedMemory]):
    """
    Given a list of memories, insert them to the database
    """

    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            models.PointStruct(
                id=uuid4().hex,
                payload={
                    "user_id": memory.user_id,
                    "memory_text": memory.memory_text,
                    "date": memory.date
                },
                vector=memory.embedding
            )
            for memory in memories
        ]
    )

async def delete_records(point_ids):
    """
    Delete a list of point ids from the database
    """

    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.PointIdsList(
            points=point_ids
        )
    )
```

Similarly, let’s write one for searching. This accepts a search vector and a user\_id, and fetches nearest neighbors to that vector.

```python
from qdrant_client.models import Distance, Filter, models

async def search_memories(
    search_vector: list[float],
    user_id: int,
    topk_neighbors=5
):
    
    # Filter by user_id
    must_conditions: list[models.Condition] = [
        models.FieldCondition(
            key="user_id",
            match=models.MatchValue(value=user_id)
        )
    ]

    outs = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=search_vector,
        with_payload=True,
        query_filter=Filter(must=must_conditions),
        score_threshold=0.1,
        limit=topk_neighbors
    )

    return [
        convert_retrieved_records(point)     
        for point in outs.points
        if point is not None
    ]
```

> Notice how we can set hybrid query filters like the `models.MatchValue` filter. Creating the index on user\_id allows us to run these queries quickly against our data. **You can extend this idea to include category tags, date ranges, and any other metadata that your application cares about.** Just make sure to create an index for faster retrieval performance.

In the next chapter, we will connect this storage layer to our agent loop using DSPy Signatures and ReAct (Reasoning and Acting).

## Memory Retrieval

In this section, we build a clean retrieval interface that pulls the most relevant, per-user memories for a given turn.

Our algorithm is simple – we will create a tool-calling chatbot agent. At every turn, the agent receives the transcript of the conversation and must generate an answer. Let’s define the DSPy signature.

```python
class ResponseGenerator(dspy.Signature):
    """
You will be given a past conversation transcript between user and an AI agent. Also the latest question by the user.
You have the option to look up the past memories from a vector database to fetch relevant context if required. 
If you can't find the answer to user's question from transcript or from your own internal knowledge, use the provided search tool calls to search for information.
You must output the final response, and also decide the latest interaction needs to be recorded into the memory database. New memories are meant to store new information that the user provides.
New memories should be made when the USER provides new info. It is not to save information about the the AI or the assistant.
    """
    transcript: list[dict] = dspy.InputField()
    question: str = dspy.InputField()

    response: str = dspy.OutputField()
    save_memory: bool = dspy.OutputField(description=
        "True if a new memory record needs to be created for the latest interaction"
                                  )
```

The docstring of the dspy Signature acts as additional instructions we pass into the LLM to help it pick its actions. Also, notice the `save_memory` flag we marked as an `OutputField`. We are asking the LLM also to output if a new memory needs to be saved because of the latest interaction with the answer.

**We also need to solve how we want to fetch relevant memories into the agent’s context.** One option is to **always** execute the search\_memories function, but there are two big problems with this:

- Not all user questions need a memory retrieval.
- While the search\_memories function expects a search vector, it is not always straightforward “ *what text we should be embedding”*. It could be the entire transcript, or just the user’s latest message, or it could be a transformation of the current conversation context.

Thankfully, we can default to **tool-calling**. When the agent thinks it lacks context to carry out a request, it can invoke a tool call to fetch relevant memories related to the conversation’s context. In DSPy, tools can be created by just writing vanilla Python function with a docstring. The LLM reads this docstring to decide when and how to call this tool.

```python
async def fetch_similar_memories(search_text: str):
        """
Search memories from vector database if conversation requires additional context.

Args:
- search_text : The string to embed and do vector similarity search
        """
        
        search_vector = (await generate_embeddings([search_text]))[0]
        memories = await search_memories(search_vector, 
                                         user_id=user_id)
        memories_str = [
            f"id={m_.id}\ntext={m_.text}\ncreated_at={m_.date}"
            for m_ in memories
        ]
        return {
            "memories": memories_str
        }
```

Note that we keep track of the user’s id externally and use it from our source of truth without asking the LLM to generate it. This guarantees isolation contextual to the current chat session.

![](https://contributor.insightmediagroup.io/wp-content/uploads/2026/01/image-6-1024x576.jpeg)

The image illustrates the process of fetching relevant memories from a vector database based on a query, which are then used by a Large Language Model (LLM) along with the current conversation.

Next, let’s create a ReAct agent with DSPy. ReAct stands for “Reasoning and Acting”. Basically, the LLM agent observes the data (in this case, the conversation history), reasons about it, and then acts

> An action can be to generate an answer directly or try to retrieve memories first.

```python
response_generator = dspy.ReAct(
        ResponseGenerator,
        tools=[fetch_similar_memories],
        max_iters=4
    )
```

In an agentic flow, the DSPy ReAct policy can craft a concise `search_text` from the current turn and the known task. The ReAct agent can call the `fetch_similar_memories` upto 4 times to search for memories before it must answer the user’s question.

### Other Retrieval Strategies

You can also choose other retrieval strategies than just similarity search. Here are some ideas:

- **Keyword Search** – Look into algorithms like BM-25 or TF-IDF
- **Category Filtering** – If you force every memory to have clear metadata tagging (like “food”, “sports”, “habits”), the agent can generate queries to search these specific subcategories instead of the whole memory stack.
- **Time Queries** – Allow the agent to retrieve records from specific time ranges!

These choices largely depend on your application.

Whatever your retrieval strategy is, once the tool fetches the LLM answers, the agent is going to generate answers from the retrieved data! Remember, it also outputs that save\_memory flag? We can trigger our custom update logic when it is turned to true.

```python
out = await response_generator.acall(
    transcript=past_messages,
    question=question,
)

response = out.response # the response
save_memory = out.save_memory # the LLM's decision to save memory or not

past_messages.extend(
    [
    {"role": "user", "content": question},
    {"role": "assistant", "content": response},
    ]
) # update conversation stack

if (save_memory): # Update memories only if LLM outputs this flag as true
    update_result = await update_memories(
        user_id=user_id,
        messages=past_messages,
    )
```

Let’s see how the update step works.

## Memory Maintenance

Memory is not a simple log of records. It is an ever-evolving pool of information. Some memories should be deleted because it is no longer relevant. Some memories must be updated because the underlying world conditions have changed.

> For example, suppose we had a memory for “user loves tea”, and we just got to know that the “user hates tea”. Instead of creating a brand new memory, we should delete the old memory and create a new one.

![](https://contributor.insightmediagroup.io/wp-content/uploads/2026/01/image-1-1024x576.jpeg)

Given a new memory and an existing vector database state, how do we determine the updated database state?

When the response generator agent decides to save new memories, we will use a separate agentic flow to decide how to do the updates. The Update memory agent receives as input the new memory, and a list of similar memories to the conversation state.

```python
.... # if save_memory is True
    response = await update_memories_agent(
        user_id=user_id,
        existing_memories=similar_memories,
        messages=messages
    )
```

Once we have decided to update the memory database, there are four logical things the memory manager agent can do:

• **add\_memory(text)**: Inserts a brand-new atomic factoid. It computes a fresh embedding and writes the record for the current user. It should also apply deduplication logic before insertion.  
• **update\_memory(id, updated\_text)**: Replaces an existing memory’s text. It deletes the old point, re-embeds the new text, and reinserts it under the same user, optionally preserving or adjusting categories. This is the canonical way to handle refinements or corrections.  
• **delete\_memories(ids):** Removes one or more memories that are no longer valid due to contradictions or obsolescence.  
• **no\_op()**: Explicitly does nothing if the maintenance agent decides that the new memory is irrelevant or already fully captured in the database state.

Again this architecture is inspired by the [Mem0 research paper](https://arxiv.org/pdf/2504.19413).

The code below shows these tools integrated into a DSPy ReAct agent with a structured signature and tool selection loop.

```python
class MemoryWithIds(BaseModel):
    memory_id: int
    memory_text: str

class UpdateMemorySignature(dspy.Signature):
    """
You will be given the conversation between user and assistant and some similar memories from the database. Your goal is to decide how to combine the new memories into the database with the existing memories.

Actions meaning:
- ADD: add new memories into the database as a new memory
- UPDATE: update an existing memory with richer information.
- DELETE: remove memory items from the database that aren't required anymore due to new information
- NOOP: No need to take any action

If no action is required you can finish.

Think less and do actions.
    """
    messages: list[dict] = dspy.InputField()
    existing_memories: list[MemoryWithIds] = dspy.InputField()
    summary: str = dspy.OutputField(
        description="Summarize what you did. Very short (less than 10 words)"
    )
```

Next, let’s write the tools our maintenance agent needs. We need functions to add, delete, update memories, and a dummy no\_op function the LLM can call when it wants to “pass”.

```python
async def update_memories_agent(
    user_id: int, 
    messages: list[dict], 
    existing_memories: list[RetrievedMemory]
):

    def get_point_id_from_memory_id(memory_id):
        return existing_memories[memory_id].point_id
        
    async def add_memory(memory_ext: str) -> str:
        """
    Add the new_memory into the database.
        """
        embeddings = await generate_embeddings(
            [memory_text]
        )
        await insert_memories(
            memories = [
                EmbeddedMemory(
                    user_id=user_id,
                    memory_text=memory_text,
                    date=datetime.now().strftime("%Y-%m-%d %H:%m"),
                    embedding=embeddings[0]
                )
            ]
        )

        return f"Memory: '{memory_text}' was added to DB"

    async def update(memory_id: int, 
                     updated_memory_text: str,
                     ):
        """
    Updating memory_id to use updated_memory_text

    Args:
    memory_id: integer index of the memory to replace

    updated_memory_text: Simple atomic factoid to replace the old memory with the new memory
        """

        point_id = get_point_id_from_memory_id(memory_id)
        await delete_records([point_id])

        embeddings = await generate_embeddings(
            [updated_memory_text]
        )
        
        await insert_memories(
            memories = [
                EmbeddedMemory(
                    user_id=user_id,
                    memory_text=updated_memory_text,
                    categories=categories,
                    date=datetime.now().strftime("%Y-%m-%d %H:%m"),
                    embedding=embeddings[0]
                )
            ]
        )
        return f"Memory {memory_id} has been updated to: '{updated_memory_text}'"

    async def noop():
        """
Call this is no action is required
        """
        return "No action done"

    async def delete(memory_ids: list[int]):
        """
    Remove these memory_ids from the database
        """        
        await delete_records(memory_ids)
        return f"Memory {memory_ids} deleted"

    memory_updater = dspy.ReAct(
        UpdateMemorySignature,
            tools=[add_memory, update, delete, noop],
            max_iters=3
    )

    out = await memory_updater.acall(
        messages=messages,
        existing_memories=memory_ids
    )
```

And that’s it! Depending on what action the ReAct agent chooses, we can simply insert, delete, update, or ignore the new memories. Below you can see a simple example of how things look when we run the code.

![](https://contributor.insightmediagroup.io/wp-content/uploads/2026/02/image-1024x755.png)

Example of how sessions with a memory-enabled agent would go. Notice we exit out of the session midway, but the agent remembers key details I had shared earlier. It can also later update these information adaptively according to conversation state!

The full version of the code also has additional features like metadata tagging for accurate retrieval which I didn’t cover in this article to keep it beginner-friendly. Be sure to check out the GitHub repo below or the YouTube tutorial to explore the full project!

## What’s next

You can watch the full video tutorial that goes into more detail about building Memory agents here.

Please accept cookies to access this content

**The code repo can be found here:** [https://github.com/avbiswas/mem0-dspy](https://github.com/avbiswas/mem0-dspy)

This tutorial explained the building blocks of a memory system. Here are some ideas on how to expand this idea:

1. **A Graph Memory system** – instead of using a vector database, store memories in a graph database. This means, your dspy modules should extract triplets instead of flat strings to represent memories.
2. **Metadata** – Alongside text, insert additional attribute filters. For example, you can group all “food” related memories. This will allow the LLM agents to query specific tags while fetching memories, instead of querying all memories at once.
3. **Optimizing prompts per user**: You can keep track of integral information in your memory database and directly inject it into the system prompt. These get passed into each message as session memory.
4. **File-Based Systems**: Another common pattern that is emerging is file-based retrieval. The core principles remain the same that we discussed here, but instead of a vector database, you can use a file system. Inserting and updating records means writing.md files. And querying will usually involve additional indexing steps or simply use tools like regex searches or grep.

**My Patreon:**  
[https://www.patreon.com/NeuralBreakdownwithAVB](https://www.patreon.com/NeuralBreakdownwithAVB)

**My YouTube channel:**  
[https://www.youtube.com/@avb\_fj](https://www.youtube.com/@avb_fj)

**Follow me on Twitter:**  
[https://x.com/neural\_avb](https://x.com/neural_avb)

**Read my articles:**