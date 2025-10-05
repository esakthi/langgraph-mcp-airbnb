import os
import json
from typing import TypedDict, List, Optional
from langchain_core.messages import AnyMessage, SystemMessage, HumanMessage, ToolMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition

from tools import search_airbnb, book_airbnb, create_google_calendar_event

# Define the state for the graph
class UserPreferences(TypedDict):
    city: str
    date_from: str
    date_to: str
    adults: int
    children: int

class AirbnbSearchResult(TypedDict):
    name: str
    price: float
    rating: float
    url: str

class AgentState(TypedDict):
    user_preferences: Optional[UserPreferences]
    search_results: Optional[List[AirbnbSearchResult]]
    chosen_airbnb: Optional[AirbnbSearchResult]
    booking_confirmation: Optional[str]
    calendar_event_id: Optional[str]
    messages: List[AnyMessage]

# Initialize the model and tools
groq_api_key = os.environ.get("GROQ_API_KEY")
model = ChatGroq(model="llama3-8b-8192", groq_api_key=groq_api_key)
tools = [search_airbnb, book_airbnb, create_google_calendar_event]
tool_executor = ToolNode(tools)

# Utility function to complete a tool call
def complete_tool_call(tool_name, **kwargs):
    tool = {
        "search_airbnb": search_airbnb,
        "book_airbnb": book_airbnb,
        "create_google_calendar_event": create_google_calendar_event,
    }[tool_name]

    observation = tool.invoke(kwargs)

    return ToolMessage(
        content=json.dumps(observation, indent=2),
        name=tool_name,
        tool_call_id="12345", # Dummy tool_call_id
    )

# Define the nodes for the graph
def get_user_preferences(state: AgentState):
    """
    Extracts user preferences from the conversation history.
    """
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an expert at extracting user preferences for booking an Airbnb. "
                "Extract the city, check-in date, check-out date, number of adults, and number of children from the user's message. "
                "If any information is missing, ask clarifying questions."
            ),
            ("user", "{input}"),
        ]
    )

    structured_llm = model.with_structured_output(UserPreferences)
    chain = prompt | structured_llm

    user_preferences = chain.invoke({"input": state["messages"][-1].content})

    return {"user_preferences": user_preferences, "messages": []}

def search_for_airbnbs(state: AgentState):
    """
    Searches for Airbnbs based on the user's preferences.
    """
    preferences = state["user_preferences"]
    tool_message = complete_tool_call("search_airbnb", **preferences)

    return {
        "search_results": json.loads(tool_message.content),
        "messages": [AIMessage(content="Searching for Airbnbs..."), tool_message],
    }

def present_choices(state: AgentState):
    """
    Presents the top 3 Airbnb choices to the user.
    """
    search_results = state["search_results"]
    message_content = "Here are the top 3 Airbnb listings I found:\n\n"
    for i, result in enumerate(search_results[:3]):
        message_content += f"{i+1}. {result['name']} - ${result['price']}/night, Rating: {result['rating']}\n"

    return {"messages": [AIMessage(content=message_content)]}

def book_airbnb_node(state: AgentState):
    """
    Books the chosen Airbnb and confirms with the user.
    """
    preferences = state["user_preferences"]
    chosen_airbnb = state["chosen_airbnb"]

    tool_message = complete_tool_call(
        "book_airbnb",
        listing_url=chosen_airbnb["url"],
        **preferences,
    )

    return {
        "booking_confirmation": json.loads(tool_message.content),
        "messages": [AIMessage(content="Booking the Airbnb..."), tool_message],
    }

def create_calendar_event_node(state: AgentState):
    """
    Creates a Google Calendar event for the booking.
    """
    preferences = state["user_preferences"]
    chosen_airbnb = state["chosen_airbnb"]

    tool_message = complete_tool_call(
        "create_google_calendar_event",
        summary=f"Airbnb Booking: {chosen_airbnb['name']}",
        start_time=f"{preferences['date_from']}T09:00:00",
        end_time=f"{preferences['date_to']}T11:00:00",
        description=f"Booking confirmation for {chosen_airbnb['name']}.",
    )

    return {
        "calendar_event_id": json.loads(tool_message.content),
        "messages": [AIMessage(content="Creating Google Calendar event..."), tool_message],
    }

# Define the graph
workflow = StateGraph(AgentState)

# Add the nodes
workflow.add_node("get_user_preferences", get_user_preferences)
workflow.add_node("search_for_airbnbs", search_for_airbnbs)
workflow.add_node("present_choices", present_choices)
workflow.add_node("book_airbnb", book_airbnb_node)
workflow.add_node("create_calendar_event", create_calendar_event_node)

# Define the edges
workflow.set_entry_point("get_user_preferences")
workflow.add_edge("search_for_airbnbs", "present_choices")

# Define conditional logic
def should_search(state: AgentState) -> str:
    """
    Determines whether to search for Airbnbs or ask for more information.
    """
    if state.get("user_preferences") and all(state["user_preferences"].values()):
        return "search"
    return "ask_for_info"

from langchain_core.pydantic_v1 import BaseModel, Field

class Choice(BaseModel):
    """A model to represent the user's choice."""
    choice: int = Field(description="The user's choice of which Airbnb to book, as an integer.")

def decide_next_step(state: AgentState) -> str:
    """
    Decides the next step based on the user's response after seeing the choices.
    """
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an expert at understanding a user's choice from a list of options. "
                "The user will be presented with a list of Airbnbs and will be asked to choose one to book. "
                "Your job is to extract the number of the choice they want to book. "
                "If the user wants to refine the search or asks for different options, you should indicate that. "
                "If the user does not want to book, you should end the conversation."
            ),
            ("user", "{input}"),
        ]
    )

    structured_llm = model.with_structured_output(Choice)
    chain = prompt | structured_llm

    last_message = state["messages"][-1].content

    try:
        user_choice = chain.invoke({"input": last_message})
        state["chosen_airbnb"] = state["search_results"][user_choice.choice - 1]
        return "book"
    except Exception:
        if "more" in last_message.lower() or "different" in last_message.lower():
            return "refine_search"
        return "end"

def should_create_calendar_event(state: AgentState) -> str:
    """
    Checks if the user wants to create a calendar event.
    """
    last_message = state["messages"][-1].content.lower()
    if "yes" in last_message or "sure" in last_message:
        return "create_event"
    return "end"

# Add conditional edges
workflow.add_conditional_edges(
    "get_user_preferences",
    should_search,
    {"search": "search_for_airbnbs", "ask_for_info": END},
)
workflow.add_conditional_edges(
    "present_choices",
    decide_next_step,
    {"book": "book_airbnb", "refine_search": "get_user_preferences", "end": END},
)
workflow.add_conditional_edges(
    "book_airbnb",
    should_create_calendar_event,
    {"create_event": "create_calendar_event", "end": END},
)
workflow.add_edge("create_calendar_event", END)


# Compile the graph
app = workflow.compile()

# Print the graph to a file for visualization
app.get_graph().print_ascii()