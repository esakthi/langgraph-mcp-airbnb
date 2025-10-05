import os
from dotenv import load_dotenv
from langchain_core.messages import AnyMessage, SystemMessage, HumanMessage
from graph import app

# Load environment variables from .env file
load_dotenv()

def main():
    """
    Main function to run the Airbnb booking agent.
    """
    # Set up the initial state
    messages: list[AnyMessage] = [
        SystemMessage(content="Hello! I'm here to help you book an Airbnb and create a Google Calendar event for your trip. What are your travel plans?")
    ]

    while True:
        # Get user input
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            break

        # Add user message to the state
        messages.append(HumanMessage(content=user_input))

        # Invoke the graph
        for event in app.stream({"messages": messages}):
            for value in event.values():
                if isinstance(value["messages"][-1], HumanMessage):
                    continue

                # Check if the message is not empty before printing
                if value["messages"][-1].content:
                    print("Assistant:", value["messages"][-1].content)

                messages.append(value["messages"][-1])


if __name__ == "__main__":
    # Check if the GROQ_API_KEY is set
    if not os.environ.get("GROQ_API_KEY"):
        raise ValueError("GROQ_API_KEY environment variable not set.")
    main()